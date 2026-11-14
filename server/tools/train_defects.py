"""Train and honestly measure the patch classifier (corrosion / leak / overheat).

    .venv/Scripts/python tools/train_defects.py --capture 0.5 --docs --refine 0,1 --seeds 0,1     compare variants
    .venv/Scripts/python tools/train_defects.py --capture 0.5 --docs --refine 1 --long 448 --save  train and save

Data
    corrosion       masks of "Corrosion in Industrial Complexes in Ostrava" (CC BY 4.0), authors' split
    leak, overheat  boxes drawn by hand on the customer's example photos (training/annotations.json)
    nothing         pages of the customer's documents (tools/build_no_defect.py), with --docs
    oil leaks       photos of power transformers with boxes (tools/fetch_transformer.py, Zenodo 22771088,
                    CC BY 4.0), with --transformer; its val and test parts are never trained on
    everyday scenes rooms, desks, laptops, people — nothing to find there (tools/fetch_scenes.py, COCO val2017,
                    freely licensed photos only), with --scenes; its test part is never trained on

Measurement
    corrosion       trained on the Ostrava train part, scored on its untouched test part
    leak, overheat  leave-one-scene-out: each scene of the customer's photos is predicted by a head
                    that never saw it (duplicates of a photo share a scene)
    false alarms    Ostrava validation + test photos (no leaks or burn marks there), the test
                    documents and the test everyday scenes (nothing at all there)
    new photos      val + test parts of the transformer set: leaks found, false alarms on photos
                    without a leak. --no-extra-train leaves its train part out, to see what it adds.

Options
    --capture P     share of training views passed through the screen-capture simulation
    --refine 1      box labels are loose; after a first pass, patches inside a box that the head
                    scores low stop counting as positives, then the head is trained again

Backbone features are cached in tmp/feat-cache, so only the first run of a data setting is slow.
"""
import argparse
import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.defect_model import PATCH, SIGNS, build_head, fit_size, load_backbone, patch_features, regions, to_batch  # noqa: E402
from capture_sim import capture  # noqa: E402

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent
OSTRAVA = ROOT / "datasets" / "ostrava"
NO_DEFECT = ROOT / "datasets" / "no_defect"
TRANSFORMER = ROOT / "datasets" / "transformer"
ANNOTATIONS = SERVER / "training" / "annotations.json"
SCENES = ROOT / "datasets" / "scenes"
CACHE = ROOT / "tmp" / "feat-cache"
OUT = SERVER / "models" / "defect-head"

SIDE = 448                       # training views are SIDE x SIDE -> 32 x 32 patches
GRID = SIDE // PATCH
LONGS = (448,)                   # frame size (long side) at inference; 630 was tried and gave nothing
IGNORE = -1.0
# Augmented views per photo, plus the whole one. Sources not listed are only evaluated, never trained on.
VIEWS = {"customer": 12, "ostrava_train": 4, "ostrava_validation": 0, "docs_train": 2, "tr_train": 1}
CACHE_VERSION = 5
# Everyday scenes are cached apart from the rest, so adding them does not recompute the other features.
SCENE_VIEWS = {"scenes_train": 3}


# ---------- views and labels ----------

def grid_uv(gh: int, gw: int, sub: int = 1):
    us = (np.arange(gw * sub) + 0.5) / (gw * sub)
    vs = (np.arange(gh * sub) + 0.5) / (gh * sub)
    return np.meshgrid(us, vs)


def identity(u, v):
    return u, v


def make_view(image: Image.Image, rng: random.Random, p_capture: float):
    """Augmented SIDE x SIDE view and its mapper: view coordinates 0..1 -> image coordinates (outside 0..1: background)."""
    w, h = image.size
    scale = rng.uniform(0.45, 1.0)
    cw, ch = w * scale, h * scale * rng.uniform(0.8, 1.0)
    left, top = rng.uniform(0, w - cw), rng.uniform(0, h - ch)
    part = image.crop((round(left), round(top), round(left + cw), round(top + ch)))
    flipped = rng.random() < 0.5
    if flipped:
        part = ImageOps.mirror(part)

    def to_image(u, v):
        return (left + (1 - u if flipped else u) * cw) / w, (top + v * ch) / h

    if rng.random() < p_capture:
        view, to_part, _ = capture(part, rng, (SIDE, SIDE))

        def mapper(u, v):
            px, py = to_part(u, v)
            off = (px < 0) | (px > 1) | (py < 0) | (py > 1)       # the room around the screen
            x, y = to_image(px, py)
            return np.where(off, -1.0, x), np.where(off, -1.0, y)
        return view, mapper

    view = part.resize((SIDE, SIDE), Image.BILINEAR)
    view = ImageEnhance.Brightness(view).enhance(rng.uniform(0.6, 1.4))
    view = ImageEnhance.Contrast(view).enhance(rng.uniform(0.7, 1.3))
    view = ImageEnhance.Color(view).enhance(rng.uniform(0.6, 1.3))
    if rng.random() < 0.3:
        view = view.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.5)))
    return view, to_image


def scene_view(image: Image.Image, rng: random.Random, _p_capture: float = 0.0):
    """A view of an everyday photo as a phone would show it: any crop, often dark, tinted or smeared by motion."""
    import cv2

    w, h = image.size
    scale = rng.uniform(0.3, 1.0)
    cw, ch = w * scale, h * scale * rng.uniform(0.6, 1.0)
    left, top = rng.uniform(0, w - cw), rng.uniform(0, h - ch)
    view = image.crop((round(left), round(top), round(left + cw), round(top + ch))).resize((SIDE, SIDE), Image.BILINEAR)
    if rng.random() < 0.5:
        view = ImageOps.mirror(view)
    view = ImageEnhance.Brightness(view).enhance(rng.uniform(0.12, 0.6) if rng.random() < 0.5 else rng.uniform(0.6, 1.3))
    view = ImageEnhance.Contrast(view).enhance(rng.uniform(0.6, 1.3))
    pixels = np.asarray(view).astype(np.float32)
    if rng.random() < 0.4:                                       # coloured light: a backlit keyboard, a screen glow
        pixels *= np.array([rng.uniform(0.6, 1.4), rng.uniform(0.6, 1.2), rng.uniform(0.6, 1.4)], dtype=np.float32)
    if rng.random() < 0.4:                                       # the hand moved
        length = rng.choice([7, 11, 15, 21])
        kernel = np.zeros((length, length), dtype=np.float32)
        if rng.random() < 0.5:
            kernel[length // 2, :] = 1.0 / length
        else:
            kernel[:, length // 2] = 1.0 / length
        pixels = cv2.filter2D(pixels, -1, kernel)
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8)), identity


def labels_from_boxes(boxes: list[dict], mapper, gh: int = GRID, gw: int = GRID) -> np.ndarray:
    """Customer photo: leak/overheat from boxes; corrosion is not annotated there -> ignored on the photo."""
    x, y = mapper(*grid_uv(gh, gw))
    on_photo = (x >= 0) & (x <= 1) & (y >= 0) & (y <= 1)
    out = np.zeros((gh, gw, len(SIGNS)), dtype=np.float32)        # off the photo: certainly nothing
    out[on_photo, SIGNS.index("corrosion")] = IGNORE
    for item in sorted(boxes, key=lambda b: b["sign"] != "ignore"):     # ignore first, positives win
        x1, y1, x2, y2 = item["box"]
        inside = (x >= x1) & (x <= x2) & (y >= y1) & (y <= y2)
        if item["sign"] == "ignore":
            out[inside, 1:] = IGNORE
        else:
            out[inside, SIGNS.index(item["sign"])] = 1.0
    return out


def labels_from_mask(mask: np.ndarray, mapper, gh: int = GRID, gw: int = GRID, sub: int = 4) -> np.ndarray:
    """Ostrava photo: corrosion from the mask (share of the patch covered); no leaks or burn marks there."""
    x, y = mapper(*grid_uv(gh, gw, sub))
    h, w = mask.shape
    on_photo = (x >= 0) & (x < 1) & (y >= 0) & (y < 1)
    values = np.where(on_photo, mask[np.clip((y * h).astype(int), 0, h - 1), np.clip((x * w).astype(int), 0, w - 1)], 0.0)
    share = values.reshape(gh, sub, gw, sub).mean(axis=(1, 3))
    out = np.zeros((gh, gw, len(SIGNS)), dtype=np.float32)
    out[..., 0] = np.where(share > 0.5, 1.0, np.where(share < 0.1, 0.0, IGNORE))
    return out


def labels_nothing(_mapper, gh: int = GRID, gw: int = GRID) -> np.ndarray:
    return np.zeros((gh, gw, len(SIGNS)), dtype=np.float32)


def transformer_items() -> list[dict]:
    """Oil leak boxes become "leak"; fire/smoke and cable-defect boxes are left undecided (they may or
    may not look like our burn marks); the rest of a photo has neither a leak nor burn marks."""
    if not (TRANSFORMER / "labels.json").exists():
        raise SystemExit("No transformer photos: run tools/fetch_transformer.py first")
    items = []
    for stem, entry in json.loads((TRANSFORMER / "labels.json").read_text(encoding="utf-8")).items():
        image = Image.open(TRANSFORMER / entry["split"] / entry["file"]).convert("RGB")
        image.thumbnail((1400, 1400))
        boxes = []
        for b in entry["boxes"]:
            if b["class"] not in ("oil_leak", "fire_smoke", "cable_defect"):
                continue
            x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in b["box"])
            # Many leak boxes are specks on a wide view of a substation, where no leak can be made out
            # at camera resolution. Only boxes of a visible size count as leaks; the specks are undecided.
            visible = b["class"] == "oil_leak" and min(x2 - x1, y2 - y1) >= 0.08 and (x2 - x1) * (y2 - y1) >= 0.02
            boxes.append({"sign": "leak" if visible else "ignore", "box": [x1, y1, x2, y2]})
        items.append({"name": f"transformer/{entry['split']}/{stem}", "group": f"tr_{entry['source']}",
                      "source": "tr_train" if entry["split"] == "train" else "tr_eval", "image": image, "boxes": boxes,
                      "label": lambda mapper, gh=GRID, gw=GRID, b=boxes: labels_from_boxes(b, mapper, gh, gw)})
    return items


def load_items(use_docs: bool, use_transformer: bool = False) -> list[dict]:
    items = []
    doc = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    for entry in doc["images"]:
        image = ImageOps.exif_transpose(Image.open(ROOT / doc["root"] / entry["file"])).convert("RGB")
        image.thumbnail((1400, 1400))
        items.append({"name": entry["file"], "group": entry["group"], "source": "customer", "image": image,
                      "boxes": entry["boxes"],
                      "label": lambda mapper, gh=GRID, gw=GRID, b=entry["boxes"]: labels_from_boxes(b, mapper, gh, gw)})
    for split in ("train", "validation", "test"):
        for path in sorted((OSTRAVA / split / "images").glob("*.jpg")):
            image = Image.open(path).convert("RGB").resize((1024, 1024), Image.BILINEAR)
            raw = Image.open(OSTRAVA / split / "masks" / f"{path.stem}.png").convert("RGB").resize((1024, 1024), Image.NEAREST)
            mask = (np.asarray(raw).max(axis=2) > 127).astype(np.float32)
            items.append({"name": f"ostrava/{split}/{path.name}", "group": f"ostrava_{split}_{path.stem}",
                          "source": f"ostrava_{split}", "image": image, "boxes": [],
                          "label": lambda mapper, gh=GRID, gw=GRID, m=mask: labels_from_mask(m, mapper, gh, gw)})
    if use_docs:
        for split in ("train", "test"):
            for path in sorted((NO_DEFECT / split).glob("*.png")):
                items.append({"name": f"no_defect/{split}/{path.name}", "group": f"docs_{split}_{path.stem}",
                              "source": f"docs_{split}", "image": Image.open(path).convert("RGB"), "boxes": [],
                              "label": labels_nothing})
        if not any(i["source"] == "docs_test" for i in items):
            raise SystemExit("No document pages: run tools/build_no_defect.py first")
    if use_transformer:
        items += transformer_items()
    return items


def extract(items, backbone, p_capture: float, seed: int) -> None:
    """Training features/labels (whole view + augmented ones) and evaluation features at every size in LONGS."""
    rng = random.Random(seed)
    dim = backbone.config.hidden_size
    for item in items:
        image = item["image"]
        views = []
        counts = {**VIEWS, **SCENE_VIEWS}
        if item["source"] in counts:
            capture_share = 0.0 if item["source"].startswith("docs") else p_capture
            make = scene_view if item["source"].startswith("scenes") else make_view
            views = [(image.resize((SIDE, SIDE), Image.BILINEAR), identity)]
            views += [make(image, rng, capture_share) for _ in range(counts[item["source"]])]
        if views:
            feats = torch.cat([patch_features(backbone, to_batch([v for v, _ in views[i:i + 16]])).cpu()
                               for i in range(0, len(views), 16)])
            item["x"] = feats.half()                                                # (V, GRID, GRID, C)
            item["y"] = torch.from_numpy(np.stack([item["label"](mapper) for _, mapper in views]))
        else:
            item["x"] = torch.empty((0, GRID, GRID, dim), dtype=torch.half)
            item["y"] = torch.empty((0, GRID, GRID, len(SIGNS)))
        item["xe"], item["ye"] = {}, {}
        for long in LONGS:
            size = fit_size(*image.size, long_side=long)
            item["xe"][long] = patch_features(backbone, to_batch([image.resize(size, Image.BILINEAR)]))[0].cpu().half()
            item["ye"][long] = torch.from_numpy(item["label"](identity, size[1] // PATCH, size[0] // PATCH))


def load_or_extract(args) -> list[dict]:
    key = json.dumps([CACHE_VERSION, args.backbone, args.capture, args.docs, args.transformer, args.data_seed, SIDE, LONGS, VIEWS,
                      hashlib.md5(ANNOTATIONS.read_bytes()).hexdigest()], sort_keys=True)
    path = CACHE / f"{hashlib.md5(key.encode()).hexdigest()[:12]}.pt"
    if path.exists():
        items = torch.load(path, weights_only=False)
    else:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        backbone = load_backbone(SERVER / "models" / args.backbone, device)
        items = load_items(args.docs, args.transformer)
        extract(items, backbone, args.capture, args.data_seed)
        for item in items:
            del item["image"], item["label"]
        CACHE.mkdir(parents=True, exist_ok=True)
        torch.save(items, path)
        del backbone
        torch.cuda.empty_cache()
    if args.scenes:
        items += scene_items(args)
    return split_new(items)


def scene_items(args) -> list[dict]:
    """Everyday photos with nothing to find, with their own cache file."""
    files = [(split, path) for split in ("train", "test") for path in sorted((SCENES / split).glob("*.jpg"))]
    if not any(split == "test" for split, _ in files):
        raise SystemExit("No everyday scenes: run tools/fetch_scenes.py first")
    key = json.dumps([CACHE_VERSION, args.backbone, args.data_seed, SIDE, LONGS, SCENE_VIEWS,
                      [f"{split}/{path.name}" for split, path in files]], sort_keys=True)
    path = CACHE / f"scenes-{hashlib.md5(key.encode()).hexdigest()[:12]}.pt"
    if path.exists():
        return torch.load(path, weights_only=False)
    items = []
    for split, file in files:
        image = ImageOps.exif_transpose(Image.open(file)).convert("RGB")
        items.append({"name": f"scenes/{split}/{file.name}", "group": f"scenes_{split}_{file.stem}",
                      "source": f"scenes_{split}", "image": image, "boxes": [], "label": labels_nothing})
    device = "cuda" if torch.cuda.is_available() else "cpu"
    backbone = load_backbone(SERVER / "models" / args.backbone, device)
    extract(items, backbone, 0.0, args.data_seed + 1000)
    for item in items:
        del item["image"], item["label"]
    torch.save(items, path)
    del backbone
    torch.cuda.empty_cache()
    return items


def split_new(items: list[dict]) -> list[dict]:
    """The transformer set's own val part is for choosing thresholds, its test part only for the final number."""
    for item in items:
        if item["source"] == "tr_eval":
            item["source"] = "tr_val" if "/val/" in item["name"] else "tr_test"
    return items


# ---------- training ----------

class Bank:
    """All training rows on the device, so a fold is just an index."""

    def __init__(self, items: list[dict], device: str):
        self.items, self.device = items, device
        self.dim = items[0]["x"].shape[-1]
        self.ranges, start = [], 0
        for item in items:
            rows = item["x"].shape[0] * GRID * GRID
            self.ranges.append((start, start + rows))
            start += rows
        # Filled photo by photo and released from RAM as it goes: the whole set does not fit twice.
        self.x = torch.empty((start, self.dim), dtype=torch.half, device=device)
        for item, (lo, hi) in zip(items, self.ranges):
            self.x[lo:hi] = item["x"].reshape(-1, self.dim).to(device)
            item["x"] = None
        self.y = torch.cat([i["y"].reshape(-1, len(SIGNS)) for i in items]).to(device)

    def rows(self, ids: list[int]) -> torch.Tensor:
        return torch.cat([torch.arange(*self.ranges[i], device=self.device) for i in ids])


def train_head(bank: Bank, ids: list[int], labels: torch.Tensor, epochs: int, seed: int) -> torch.nn.Module:
    rows = bank.rows(ids)
    y = labels[rows]
    positives = (y == 1).sum(0).clamp(min=1).float()
    negatives = (y == 0).sum(0).float()
    pos_weight = (negatives / positives).clamp(max=20)                # rare signs must not be drowned out

    torch.manual_seed(seed)
    head = build_head(bank.dim).to(bank.device)
    optimizer = torch.optim.AdamW(head.parameters(), lr=1e-3, weight_decay=1e-2)
    batch = 8192
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=2e-3, total_steps=epochs * -(-len(rows) // batch))
    head.train()
    for _ in range(epochs):
        for idx in rows[torch.randperm(len(rows), device=bank.device)].split(batch):
            yb = labels[idx]
            mask = (yb != IGNORE).float()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                head(bank.x[idx].float()), yb.clamp(min=0), weight=mask, pos_weight=pos_weight,
                reduction="sum") / mask.sum()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            schedule.step()
    return head.eval()


@torch.inference_mode()
def refine_labels(head, bank: Bank, ids: list[int], labels: torch.Tensor) -> None:
    """Inside a hand-drawn box most patches are background. Keep as positives the patches the head
    scores at least 0.5, and in any case the better half of the box; the rest stops counting."""
    for i in ids:
        if bank.items[i]["source"] not in ("customer", "tr_train"):
            continue
        start, end = bank.ranges[i]
        prob = torch.sigmoid(head(bank.x[start:end].float())).view(-1, GRID * GRID, len(SIGNS))
        y = labels[start:end].view(-1, GRID * GRID, len(SIGNS))            # a view: edits go to `labels`
        for k in (1, 2):
            for view in range(y.shape[0]):
                positive = y[view, :, k] == 1
                if positive.any():
                    cut = min(0.5, prob[view, positive, k].median().item())
                    y[view, positive & (prob[view, :, k] < cut), k] = IGNORE


def fit(bank: Bank, ids: list[int], refine: bool, epochs: int, seed: int) -> torch.nn.Module:
    if not refine:
        return train_head(bank, ids, bank.y, epochs, seed)
    labels = bank.y.clone()
    refine_labels(train_head(bank, ids, labels, epochs, seed), bank, ids, labels)
    return train_head(bank, ids, labels, epochs, seed)


@torch.inference_mode()
def logits(head, item: dict, long: int, device: str) -> np.ndarray:
    return head(item["xe"][long].to(device).float()).clamp(-12, 12).cpu().numpy()


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


# ---------- measurement ----------

def overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def score_image(boxes: list[dict], prob: np.ndarray, thresholds: dict) -> dict:
    """Per sign: was an annotated area found; was anything raised outside annotated and ignored areas."""
    out = {}
    for k, sign in enumerate(SIGNS):
        truth = [b["box"] for b in boxes if b["sign"] == sign]
        tolerated = truth + [b["box"] for b in boxes if b["sign"] == "ignore"]
        found = regions(prob[..., k], thresholds[sign])
        out[sign] = {"has": bool(truth), "hit": any(overlaps(r["box"], t) for r in found for t in truth),
                     "false": any(not any(overlaps(r["box"], t) for t in tolerated) for r in found)}
    return out


def average_precision(score: np.ndarray, truth: np.ndarray) -> float:
    order = np.argsort(-score)
    hit = truth[order]
    precision = np.cumsum(hit) / (np.arange(len(hit)) + 1)
    return float((precision * hit).sum() / max(1, hit.sum()))


def evaluate(bank: Bank, refine: bool, epochs: int, seed: int, extra_train: bool = True) -> dict:
    """Out-of-sample logit maps for every evaluation image: {name: {long: (h, w, signs)}}."""
    items = bank.items
    by_source = lambda *sources: [i for i, item in enumerate(items) if item["source"] in sources]
    customer = by_source("customer")
    base = by_source("ostrava_train", "docs_train", "scenes_train", *(("tr_train",) if extra_train else ()))
    out = {}

    head = fit(bank, customer + base, refine, epochs, seed)
    for i in by_source("ostrava_validation", "ostrava_test", "docs_test", "scenes_test", "tr_val", "tr_test"):
        out[items[i]["name"]] = {long: logits(head, items[i], long, bank.device) for long in LONGS}
    if True:
        for group in sorted({items[i]["group"] for i in customer}):
            held = [i for i in customer if items[i]["group"] == group]
            head = fit(bank, [i for i in customer if i not in held] + base, refine, epochs, seed)
            for i in held:
                out[items[i]["name"]] = {long: logits(head, items[i], long, bank.device) for long in LONGS}
    return out


def summarise(items: list[dict], maps: dict, long: int, thresholds: dict) -> dict:
    report = {}
    scored = {item["name"]: score_image(item["boxes"], sigmoid(maps[item["name"]][long]), thresholds)
              for item in items if item["name"] in maps}
    source = {item["name"]: item["source"] for item in items}

    for prefix, wanted in (("", "customer"), ("new_val_", "tr_val"), ("new_test_", "tr_test")):
        names = [n for n in scored if source[n] == wanted]
        for k, sign in enumerate(SIGNS[1:], 1):
            positive = [scored[n][sign] for n in names if scored[n][sign]["has"]]
            negative = [scored[n][sign] for n in names if not scored[n][sign]["has"]]
            pairs = [(maps[i["name"]][long][..., k].ravel(), i["ye"][long][..., k].numpy().ravel())
                     for i in items if i["name"] in names]
            if pairs:
                z, y = np.concatenate([p[0] for p in pairs]), np.concatenate([p[1] for p in pairs])
                report[prefix + sign] = {"found": f"{sum(p['hit'] for p in positive)}/{len(positive)}",
                                         "false_images": f"{sum(n['false'] for n in negative)}/{len(negative)}",
                                         "patch_ap": round(average_precision(z[y != IGNORE], y[y != IGNORE]), 3)}

    for label, sources in (("ostrava_no_leak_or_burn", ("ostrava_validation", "ostrava_test")), ("documents", ("docs_test",)),
                           ("everyday_scenes", ("scenes_test",))):
        names = [n for n in scored if source[n] in sources]
        if names:
            signs = SIGNS[1:] if label.startswith("ostrava") else SIGNS
            report[f"false_on_{label}"] = {
                **{sign: sum(scored[n][sign]["false"] for n in names) for sign in signs},
                "any": sum(any(scored[n][s]["false"] for s in signs) for n in names), "of": len(names)}

    for split in ("validation", "test"):
        tp = fp = fn = 0
        for item in items:
            if item["source"] == f"ostrava_{split}" and item["name"] in maps:
                pred = sigmoid(maps[item["name"]][long][..., 0]) > thresholds["corrosion"]
                truth = item["ye"][long][..., 0].numpy() == 1
                tp += int((pred & truth).sum()); fp += int((pred & ~truth).sum()); fn += int((~pred & truth).sum())
        report[f"corrosion_{split}"] = {"iou": round(tp / max(1, tp + fp + fn), 3),
                                        "precision": round(tp / max(1, tp + fp), 3),
                                        "recall": round(tp / max(1, tp + fn), 3)}
    return report


def fit_platt(z: np.ndarray, y: np.ndarray) -> list[float]:
    """p = sigmoid(a * logit + b) fitted on out-of-sample patches; pulled gently towards a=1, b=0."""
    zt, yt = torch.tensor(z, dtype=torch.float64), torch.tensor(y, dtype=torch.float64)
    params = torch.tensor([1.0, 0.0], dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([params], max_iter=200)

    def closure():
        optimizer.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(params[0] * zt + params[1], yt)
        loss = loss + 1e-3 * ((params[0] - 1) ** 2 + params[1] ** 2)
        loss.backward()
        return loss

    optimizer.step(closure)
    return [round(v, 4) for v in params.tolist()]


def calibration(items: list[dict], maps: dict, long: int) -> dict:
    """Leak and overheat from the left-out customer scenes, corrosion from Ostrava validation."""
    out = {}
    for k, sign in enumerate(SIGNS):
        source = "ostrava_validation" if sign == "corrosion" else "customer"
        pairs = [(maps[i["name"]][long][..., k].ravel(), i["ye"][long][..., k].numpy().ravel())
                 for i in items if i["source"] == source and i["name"] in maps]
        z, y = np.concatenate([p[0] for p in pairs]), np.concatenate([p[1] for p in pairs])
        out[sign] = fit_platt(z[y != IGNORE], y[y != IGNORE])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="dinov2-base")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--capture", type=float, default=0.0)
    parser.add_argument("--docs", action="store_true")
    parser.add_argument("--transformer", action="store_true", help="use the transformer oil-leak photos")
    parser.add_argument("--scenes", action="store_true", help="use everyday photos with nothing to find (tools/fetch_scenes.py)")
    parser.add_argument("--no-extra-train", action="store_true", help="keep their train part out of training")
    parser.add_argument("--data-seed", type=int, default=0)
    parser.add_argument("--refine", default="0", help="comma list of 0/1")
    parser.add_argument("--seeds", default="0", help="comma list of head seeds")
    parser.add_argument("--thresholds", default="0.5,0.5,0.5", help="corrosion,leak,overheat")
    parser.add_argument("--leak-thresholds", default="", help="extra leak thresholds to print a short line for")
    parser.add_argument("--long", type=int, default=448, help="inference long side used with --save")
    parser.add_argument("--save", action="store_true", help="train the final head with the first refine/seed and save it")
    args = parser.parse_args()
    thresholds = dict(zip(SIGNS, map(float, args.thresholds.split(","))))
    started = time.time()

    items = load_or_extract(args)
    bank = Bank(items, "cuda" if torch.cuda.is_available() else "cpu")
    print(f"features: {len(items)} photos, {bank.x.shape[0]} patches, {time.time() - started:.0f}s", flush=True)

    first = None
    for refine in [bool(int(v)) for v in args.refine.split(",")]:
        for seed in map(int, args.seeds.split(",")):
            maps = evaluate(bank, refine, args.epochs, seed, extra_train=not args.no_extra_train)
            first = first or (refine, seed, maps)
            for long in LONGS:
                print(json.dumps({"capture": args.capture, "docs": args.docs, "extra": not args.no_extra_train,
                                  "refine": int(refine), "seed": seed,
                                  "long": long, **summarise(items, maps, long, thresholds)}, ensure_ascii=False), flush=True)
            for value in filter(None, args.leak_thresholds.split(",")):
                r = summarise(items, maps, LONGS[0], {**thresholds, "leak": float(value)})
                print(f"   refine={int(refine)} leak>{value}: customer {r['leak']['found']} false {r['leak']['false_images']}"
                      f" | new val {r['new_val_leak']['found']} false {r['new_val_leak']['false_images']}"
                      f" | new test {r['new_test_leak']['found']} false {r['new_test_leak']['false_images']}"
                      f" | docs {r['false_on_documents']['leak']} | ostrava {r['false_on_ostrava_no_leak_or_burn']['leak']}", flush=True)
            print(f"  {time.time() - started:.0f}s", flush=True)

    if args.save:
        refine, seed, maps = first
        # Everything except the two untouched test parts.
        held_out = ("ostrava_test", "docs_test", "scenes_test", "tr_val", "tr_test") + (("tr_train",) if args.no_extra_train else ())
        final_ids = [i for i, item in enumerate(items) if item["source"] not in held_out]
        head = fit(bank, final_ids, refine, args.epochs, seed)
        OUT.mkdir(parents=True, exist_ok=True)
        torch.save(head.state_dict(), OUT / "head.pt")
        meta = {"backbone": args.backbone, "dim": bank.dim, "signs": SIGNS, "thresholds": thresholds,
                "long_side": args.long, "calibration": calibration(items, maps, args.long),
                "training": {"capture": args.capture, "docs": args.docs, "transformer": args.transformer, "scenes": args.scenes,
                             "refine": int(refine), "seed": seed,
                             "epochs": args.epochs},
                "measured": summarise(items, maps, args.long, thresholds)}
        (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"saved {OUT}\n{json.dumps(meta['calibration'])}")


if __name__ == "__main__":
    main()
