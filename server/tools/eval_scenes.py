"""How often a saved head raises a false alarm on photos where there is nothing to find.

    .venv/Scripts/python tools/eval_scenes.py [head_dir] [--show folder_with_frames ...]

Counts, per sign, the photos of datasets/scenes/test with at least one detection. Photos given with
--show (for example frames a phone saved into server/data/photos) are listed one by one.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from app.vision import HEAD_DIR, PatchDetector  # noqa: E402

SCENES = SERVER.parent / "datasets" / "scenes" / "test"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("head", nargs="?", default=str(HEAD_DIR))
    parser.add_argument("--show", nargs="*", default=[])
    args = parser.parse_args()
    detector = PatchDetector(Path(args.head))

    files = sorted(SCENES.glob("*.jpg"))
    false = {"corrosion": 0, "leak": 0, "overheat": 0, "any": 0}
    for path in files:
        labels = {d.label for d in detector.detect(Image.open(path))}
        for label in labels:
            false[label] += 1
        false["any"] += bool(labels)
    print(f"{args.head}: false alarms on {len(files)} everyday photos: {false}")

    for folder in args.show:
        for path in sorted(Path(folder).glob("*.jpg")):
            found = [f"{d.label} {d.score:.2f} {len(d.cells)} cells" for d in detector.detect(Image.open(path))]
            print(f"  {path.name[:8]}: {found or 'nothing'}")


if __name__ == "__main__":
    main()
