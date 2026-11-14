"""Frame analysis.

`Detector.detect` is the single seam for the vision model. Boxes are normalised to 0..1
of the frame so the client does not care about the resolution it sent.
"""
import io
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Protocol

import numpy as np
from PIL import Image

from .config import SERVER_DIR

log = logging.getLogger("uvicorn.error")
MODELS_DIR = SERVER_DIR / "models"
HEAD_DIR = MODELS_DIR / "defect-head"

SIGN_TITLES = {"corrosion": "Коррозия", "leak": "Течь", "overheat": "Следы перегрева"}


@dataclass
class Detection:
    label: str          # machine name of the visual sign: corrosion / leak / overheat
    title: str          # text shown on the AR label
    score: float        # model confidence 0..1
    box: tuple[float, float, float, float]  # x1, y1, x2, y2, normalised
    cells: list[tuple[int, int]] | None = None   # (column, row) patches of the area, for the AR highlight
    grid: tuple[int, int] | None = None          # patch grid size (columns, rows) the cells refer to

    def to_dict(self) -> dict:
        return asdict(self)


class Detector(Protocol):
    is_stub: bool

    def detect(self, image: Image.Image) -> list[Detection]: ...


class ColorStubDetector:
    """Placeholder, NOT a model: boxes the rust-coloured area of the frame.

    It exists only to prove the camera -> server -> overlay path end to end.
    """

    is_stub = True

    def detect(self, image: Image.Image) -> list[Detection]:
        small = image.convert("RGB").resize((160, 120))
        h, s, v = (np.asarray(c, dtype=np.int16) for c in small.convert("HSV").split())
        # PIL hue is 0..255; ~10..35 degrees of orange-brown is roughly 7..25.
        mask = (h >= 7) & (h <= 25) & (s >= 110) & (v >= 60) & (v <= 235)
        share = float(mask.mean())
        if share < 0.01:
            return []
        ys, xs = np.nonzero(mask)
        x1, x2 = np.percentile(xs, [5, 95]) / mask.shape[1]
        y1, y2 = np.percentile(ys, [5, 95]) / mask.shape[0]
        score = min(0.99, 0.3 + share * 3)
        return [Detection("corrosion", "Коррозия (заглушка по цвету)", round(score, 2),
                          (round(float(x1), 4), round(float(y1), 4), round(float(x2), 4), round(float(y2), 4)))]


class PatchDetector:
    """Frozen DINOv2 backbone + the small trained head from server/models/defect-head.

    Everything is loaded from disk; nothing goes to the network. See tools/train_defects.py
    for how the head is trained and measured.
    """

    is_stub = False

    def __init__(self, head_dir=HEAD_DIR, device: str | None = None):
        import torch

        from . import defect_model

        self.torch, self.dm = torch, defect_model
        self.meta = json.loads((head_dir / "meta.json").read_text(encoding="utf-8"))
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.backbone = defect_model.load_backbone(MODELS_DIR / self.meta["backbone"], self.device)
        self.head = defect_model.build_head(self.meta["dim"])
        self.head.load_state_dict(torch.load(head_dir / "head.pt", map_location="cpu"))
        self.head.to(self.device).eval()
        self.lock = threading.Lock()   # one frame on the GPU at a time

    def detect(self, image: Image.Image) -> list[Detection]:
        dm = self.dm
        size = dm.fit_size(*image.size, long_side=self.meta.get("long_side", 448))
        frame = image.convert("RGB").resize(size, Image.BILINEAR)
        with self.lock, self.torch.inference_mode():
            features = dm.patch_features(self.backbone, dm.to_batch([frame]))[0]
            logits = self.head(features).clamp(-12, 12).cpu().numpy()
        prob = 1 / (1 + np.exp(-logits))

        grid = (prob.shape[1], prob.shape[0])
        found = []
        for index, sign in enumerate(dm.SIGNS):
            # Raw probabilities are overconfident (rare signs are up-weighted in training), so the
            # number shown to the engineer comes from a calibration fitted on out-of-sample patches.
            scale, shift = self.meta.get("calibration", {}).get(sign, (1.0, 0.0))
            confidence = 1 / (1 + np.exp(-(scale * logits[..., index] + shift)))
            for region in dm.regions(prob[..., index], self.meta["thresholds"][sign], confidence=confidence):
                box = tuple(round(float(v), 4) for v in region["box"])
                found.append(Detection(sign, SIGN_TITLES[sign], round(region["score"], 2), box, region["cells"], grid))
        return found


def load_detector() -> Detector:
    """The trained model when its files are present, the stub otherwise (or with EQUIPVISION_DETECTOR=stub)."""
    if os.environ.get("EQUIPVISION_DETECTOR") == "stub":
        return ColorStubDetector()
    if not (HEAD_DIR / "head.pt").exists():
        log.warning("Нет обученной головы в %s — работает заглушка. Запустите tools/train_defects.py", HEAD_DIR)
        return ColorStubDetector()
    try:
        detector = PatchDetector()
    except Exception as exc:  # missing torch or backbone, no memory: the app must still start
        log.warning("Модель распознавания не загрузилась (%s) — работает заглушка. "
                    "Веса основы: tools/fetch_model.py", exc)
        return ColorStubDetector()
    log.info("Модель распознавания: %s + голова, устройство %s", detector.meta["backbone"], detector.device)
    return detector


def decode_jpeg(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image
