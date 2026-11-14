"""Train the corrosion segmentation model on "Corrosion in Industrial Complexes in Ostrava".

    .venv/Scripts/python tools/train_corrosion.py [--epochs 60]

Dataset: https://zenodo.org/records/11235637 (CC BY 4.0), expected in datasets/ostrava with the
authors' own train / validation / test folders — the split is kept as published, so no image
leaks between parts. Masks are RGB: green = corrosion, black = background.

Model: torchvision DeepLabV3 with a MobileNetV3-Large backbone (BSD-3), pretrained weights
fetched by torchvision on first run. Output: server/models/corrosion-seg/{model.pt,meta.json}.
"""
import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageEnhance, ImageFilter
from torch.utils.data import DataLoader, Dataset
from torchvision.models.segmentation import DeepLabV3_MobileNet_V3_Large_Weights, deeplabv3_mobilenet_v3_large

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "datasets" / "ostrava"
OUT = ROOT / "server" / "models" / "corrosion-seg"
CROP = 512            # training crop, pixels
EVAL_SIDE = 768       # images are evaluated whole at this size
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def load_pair(split: str, name: str) -> tuple[Image.Image, Image.Image]:
    image = Image.open(DATA / split / "images" / f"{name}.jpg").convert("RGB")
    mask = Image.open(DATA / split / "masks" / f"{name}.png").convert("RGB")
    binary = Image.fromarray((np.asarray(mask).max(axis=2) > 127).astype(np.uint8) * 255)
    return image, binary


def to_tensors(image: Image.Image, mask: Image.Image) -> tuple[torch.Tensor, torch.Tensor]:
    x = torch.from_numpy(np.asarray(image, dtype=np.float32) / 255).permute(2, 0, 1)
    x = (x - torch.tensor(MEAN).view(3, 1, 1)) / torch.tensor(STD).view(3, 1, 1)
    y = torch.from_numpy((np.asarray(mask) > 127).astype(np.float32)).unsqueeze(0)
    return x, y


class Crops(Dataset):
    """Random crops with the distortions a phone camera adds when it looks at a screen or a poster."""

    def __init__(self, split: str, per_image: int):
        names = sorted(p.stem for p in (DATA / split / "images").glob("*.jpg"))
        # Decode once, at half resolution: 2048 px originals are far more than a 640 px camera frame shows.
        self.pairs = []
        for name in names:
            image, mask = load_pair(split, name)
            self.pairs.append((image.resize((1024, 1024), Image.BILINEAR), mask.resize((1024, 1024), Image.NEAREST)))
        self.per_image = per_image

    def __len__(self) -> int:
        return len(self.pairs) * self.per_image

    def __getitem__(self, index: int):
        image, mask = self.pairs[index % len(self.pairs)]
        side = random.randint(384, 1024)                      # zoom: close-up detail .. whole scene
        left, top = random.randint(0, 1024 - side), random.randint(0, 1024 - side)
        box = (left, top, left + side, top + side)
        image = image.crop(box).resize((CROP, CROP), Image.BILINEAR)
        mask = mask.crop(box).resize((CROP, CROP), Image.NEAREST)
        if random.random() < 0.5:
            image, mask = image.transpose(Image.FLIP_LEFT_RIGHT), mask.transpose(Image.FLIP_LEFT_RIGHT)
        image = ImageEnhance.Brightness(image).enhance(random.uniform(0.6, 1.4))
        image = ImageEnhance.Contrast(image).enhance(random.uniform(0.7, 1.3))
        image = ImageEnhance.Color(image).enhance(random.uniform(0.6, 1.3))
        if random.random() < 0.3:
            image = image.filter(ImageFilter.GaussianBlur(random.uniform(0.5, 2.0)))
        return to_tensors(image, mask)


def build_model() -> torch.nn.Module:
    model = deeplabv3_mobilenet_v3_large(weights=DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT)
    model.classifier[4] = torch.nn.Conv2d(256, 1, kernel_size=1)   # one class: corrosion
    model.aux_classifier = None
    return model


def loss_fn(logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    bce = F.binary_cross_entropy_with_logits(logits, target)
    prob = torch.sigmoid(logits)
    inter = (prob * target).sum(dim=(1, 2, 3))
    dice = 1 - (2 * inter + 1) / (prob.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + dice.mean()


@torch.inference_mode()
def evaluate(model: torch.nn.Module, split: str, device: str) -> dict:
    """Pixel IoU / Dice / precision / recall over the whole split (pixels pooled across images)."""
    model.eval()
    tp = fp = fn = 0
    for path in sorted((DATA / split / "images").glob("*.jpg")):
        image, mask = load_pair(split, path.stem)
        x, y = to_tensors(image.resize((EVAL_SIDE, EVAL_SIDE), Image.BILINEAR),
                          mask.resize((EVAL_SIDE, EVAL_SIDE), Image.NEAREST))
        pred = torch.sigmoid(model(x.unsqueeze(0).to(device))["out"])[0, 0].cpu() > 0.5
        truth = y[0] > 0.5
        tp += int((pred & truth).sum()); fp += int((pred & ~truth).sum()); fn += int((~pred & truth).sum())
    return {
        "iou": round(tp / max(1, tp + fp + fn), 4),
        "dice": round(2 * tp / max(1, 2 * tp + fp + fn), 4),
        "precision": round(tp / max(1, tp + fp), 4),
        "recall": round(tp / max(1, tp + fn), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    loader = DataLoader(Crops("train", per_image=4), batch_size=args.batch, shuffle=True, drop_last=True)
    model = build_model().to(device)
    optimizer = torch.optim.AdamW([
        {"params": model.backbone.parameters(), "lr": 1e-4},
        {"params": model.classifier.parameters(), "lr": 1e-3},
    ], weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=[1e-4, 1e-3],
                                                   total_steps=args.epochs * len(loader))
    scaler = torch.amp.GradScaler(enabled=device == "cuda")

    OUT.mkdir(parents=True, exist_ok=True)
    best, started = -1.0, time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            with torch.autocast(device_type=device, enabled=device == "cuda"):
                loss = loss_fn(model(x)["out"], y)
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(optimizer); scaler.update(); schedule.step()
            total += loss.item()
        if epoch % 5 == 0 or epoch == args.epochs:
            val = evaluate(model, "validation", device)
            print(f"epoch {epoch:3d}  loss {total / len(loader):.3f}  val IoU {val['iou']:.3f}  "
                  f"recall {val['recall']:.3f}  {time.time() - started:.0f}s", flush=True)
            if val["iou"] > best:        # model choice uses validation only; test stays untouched
                best = val["iou"]
                torch.save(model.state_dict(), OUT / "model.pt")

    model.load_state_dict(torch.load(OUT / "model.pt", map_location=device))
    meta = {
        "model": "torchvision deeplabv3_mobilenet_v3_large, 1 class",
        "dataset": "Corrosion in Industrial Complexes in Ostrava, zenodo.org/records/11235637, CC BY 4.0",
        "train_images": 75, "epochs": args.epochs, "seed": args.seed, "eval_side": EVAL_SIDE,
        "validation": evaluate(model, "validation", device),
        "test": evaluate(model, "test", device),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
