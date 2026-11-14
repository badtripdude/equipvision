"""Patch classifier on frozen DINOv2 features: the vision model of the prototype.

The backbone (DINOv2, Apache-2.0) is never trained. A small head scores every 14x14 px
patch of the frame for each visual sign; neighbouring positive patches are merged into
boxes. Used both by the training tool and by the server, so the two cannot drift apart.
"""
from collections import deque
from pathlib import Path

import numpy as np
import torch
from PIL import Image

SIGNS = ("corrosion", "leak", "overheat")
PATCH = 14
MEAN = torch.tensor((0.485, 0.456, 0.406)).view(1, 3, 1, 1)
STD = torch.tensor((0.229, 0.224, 0.225)).view(1, 3, 1, 1)


def load_backbone(model_dir: Path, device: str):
    from transformers import AutoModel

    model = AutoModel.from_pretrained(model_dir, local_files_only=True).to(device).eval()
    if device == "cuda":
        model = model.half()
    return model


def to_batch(images: list[Image.Image]) -> torch.Tensor:
    """RGB images of one size (sides divisible by 14) -> normalised float tensor."""
    array = np.stack([np.asarray(im.convert("RGB"), dtype=np.float32) / 255 for im in images])
    return (torch.from_numpy(array).permute(0, 3, 1, 2) - MEAN) / STD


@torch.inference_mode()
def patch_features(backbone, batch: torch.Tensor) -> torch.Tensor:
    """(N, 3, H, W) -> (N, H/14, W/14, C) patch tokens."""
    param = next(backbone.parameters())
    tokens = backbone(pixel_values=batch.to(param.device, param.dtype)).last_hidden_state[:, 1:, :]
    n, _, h, w = batch.shape
    return tokens.reshape(n, h // PATCH, w // PATCH, -1).float()


def build_head(dim: int) -> torch.nn.Module:
    return torch.nn.Sequential(
        torch.nn.Linear(dim, 256), torch.nn.GELU(), torch.nn.Dropout(0.2), torch.nn.Linear(256, len(SIGNS)))


def fit_size(width: int, height: int, long_side: int = 448) -> tuple[int, int]:
    """Size for inference: long side ~long_side, aspect kept, both sides divisible by the patch."""
    scale = long_side / max(width, height)
    return (max(PATCH, round(width * scale / PATCH) * PATCH), max(PATCH, round(height * scale / PATCH) * PATCH))


def smooth(prob: np.ndarray) -> np.ndarray:
    """3x3 mean: a lone hot patch is usually noise, a defect covers several."""
    padded = np.pad(prob, 1, mode="edge")
    return sum(padded[dy:dy + prob.shape[0], dx:dx + prob.shape[1]] for dy in range(3) for dx in range(3)) / 9


def regions(prob: np.ndarray, threshold: float, min_patches: int = 3, keep: int = 3,
            confidence: np.ndarray | None = None) -> list[dict]:
    """Connected areas above the threshold -> [{box (normalised x1,y1,x2,y2), score, patches}], best first.

    Areas are cut out of `prob`; the reported score is read from `confidence` (calibrated
    probabilities) when it is given.
    """
    confidence = prob if confidence is None else confidence
    h, w = prob.shape
    mask = smooth(prob) > threshold
    seen = np.zeros_like(mask)
    found = []
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        queue, cells = deque([(sy, sx)]), []
        seen[sy, sx] = True
        while queue:
            y, x = queue.popleft()
            cells.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if len(cells) < min_patches:
            continue
        ys, xs = zip(*cells)
        values = np.sort(confidence[list(ys), list(xs)])[::-1]
        score = float(values[: max(3, len(values) // 4)].mean())      # confidence of the strongest quarter
        found.append({"box": (min(xs) / w, min(ys) / h, (max(xs) + 1) / w, (max(ys) + 1) / h),
                      "score": score, "patches": len(cells), "cells": [(int(x), int(y)) for y, x in cells]})
    return sorted(found, key=lambda r: -r["score"])[:keep]
