"""Draw a 10x10 grid with axis numbers over photos, to read box coordinates off them when annotating.

    .venv/Scripts/python tools/grid_sheets.py OUT_DIR IMAGE [IMAGE ...]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CELL = 700


def font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in sys.argv[2:]]
    for start in range(0, len(paths), 4):
        chunk = paths[start:start + 4]
        sheet = Image.new("RGB", (2 * CELL, ((len(chunk) + 1) // 2) * CELL), "white")
        for i, path in enumerate(chunk):
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            image.thumbnail((CELL - 50, CELL - 60))
            tile = Image.new("RGB", (CELL, CELL), "white")
            ox, oy = 30, 40
            tile.paste(image, (ox, oy))
            draw = ImageDraw.Draw(tile)
            draw.text((4, 4), f"[{start + i}] {path.parent.name[:12]}/{path.name[:40]}".encode("ascii", "replace").decode(),
                      fill="black", font=font(15))
            for k in range(11):
                x, y = ox + image.width * k / 10, oy + image.height * k / 10
                draw.line((x, oy, x, oy + image.height), fill=(0, 255, 255), width=1)
                draw.line((ox, y, ox + image.width, y), fill=(0, 255, 255), width=1)
                draw.text((x - 4, oy - 16), str(k), fill="blue", font=font(13))
                draw.text((ox - 20, y - 7), str(k), fill="blue", font=font(13))
            sheet.paste(tile, ((i % 2) * CELL, (i // 2) * CELL))
        sheet.save(out / f"grid_{start // 4 + 1}.jpg", quality=88)
        print(f"grid_{start // 4 + 1}.jpg", [p.name for p in chunk])


if __name__ == "__main__":
    main()
