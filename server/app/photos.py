"""Photos of findings: the camera frame on which a defect was recorded, with its area outlined.

Taken automatically when the camera records a sign, or from the live frame when the engineer
records one by hand. Kept next to the database; they go into the history and the report.
"""
import re
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from .config import DATA_DIR

PHOTO_DIR = DATA_DIR / "photos"
NAME = re.compile(r"^[0-9a-f]{32}\.jpg$")
COLORS = {"corrosion": (255, 159, 26), "leak": (41, 182, 246), "overheat": (255, 82, 82)}
MAX_SIDE = 960


def save(image: Image.Image, detections=()) -> str:
    """Store the frame (downscaled) with the detected areas outlined; returns the file name."""
    picture = image.convert("RGB")
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    draw = ImageDraw.Draw(picture)
    for d in detections:
        x1, y1, x2, y2 = d.box
        draw.rectangle((x1 * picture.width, y1 * picture.height, x2 * picture.width, y2 * picture.height),
                       outline=COLORS.get(d.label, (255, 0, 255)), width=4)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    picture.save(PHOTO_DIR / name, "JPEG", quality=82)
    return name


def path(name: str | None) -> Path | None:
    """Path of a stored photo, or None for anything that is not one of our file names."""
    if not name or not NAME.match(name):
        return None
    target = PHOTO_DIR / name
    return target if target.exists() else None


def delete(name: str | None) -> None:
    target = path(name)
    if target:
        target.unlink(missing_ok=True)
