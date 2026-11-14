"""Run the detector over folders of photos and save contact sheets with the boxes drawn.

    .venv/Scripts/python tools/eval_photos.py OUT_DIR IMAGE_DIR [IMAGE_DIR ...]

Writes OUT_DIR/<folder>_<n>.jpg sheets and OUT_DIR/results.json. Used to look at what the
model finds on the customer's example photos; it is a review aid, not an accuracy metric.
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.vision import load_detector  # noqa: E402

CELL, COLS, PER_SHEET = 500, 3, 6
COLORS = {"corrosion": (255, 140, 0), "leak": (0, 170, 255), "overheat": (255, 40, 40)}


def font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    detector = load_detector()
    results = {}
    for folder_index, folder in enumerate(map(Path, sys.argv[2:])):
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        for start in range(0, len(files), PER_SHEET):
            chunk = files[start:start + PER_SHEET]
            rows = (len(chunk) + COLS - 1) // COLS
            sheet = Image.new("RGB", (COLS * CELL, rows * (CELL + 22)), "white")
            pen = ImageDraw.Draw(sheet)
            for i, path in enumerate(chunk):
                image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
                started = time.perf_counter()
                found = detector.detect(image)
                ms = (time.perf_counter() - started) * 1000
                results[f"{folder.name}/{path.name}"] = {
                    "ms": round(ms), "detections": [d.to_dict() for d in found]}

                view = image.copy()
                view.thumbnail((CELL - 6, CELL - 6))
                draw = ImageDraw.Draw(view)
                for d in found:
                    x1, y1, x2, y2 = (d.box[0] * view.width, d.box[1] * view.height,
                                      d.box[2] * view.width, d.box[3] * view.height)
                    color = COLORS.get(d.label, (255, 0, 255))
                    draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
                    tag = f"{d.label} {d.score:.2f}"
                    draw.rectangle((x1, y1, x1 + 8 * len(tag), y1 + 15), fill=color)
                    draw.text((x1 + 2, y1 + 1), tag, fill="black", font=font(13))
                x, y = (i % COLS) * CELL, (i // COLS) * (CELL + 22)
                sheet.paste(view, (x + 3, y + 22))
                summary = ", ".join(f"{d.label} {d.score:.2f}" for d in found) or "nothing"
                pen.text((x + 3, y + 3), f"{start + i + 1}: {summary} ({ms:.0f} ms)"[:70], fill="black", font=font(14))
            name = f"f{folder_index}_{start // PER_SHEET + 1}.jpg"
            sheet.save(out / name, quality=85)
            print(name, [p.name for p in chunk])
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
