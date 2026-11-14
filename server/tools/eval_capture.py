"""How a saved model copes with the demo condition: the camera looks at the example photos on a screen.

    .venv/Scripts/python tools/eval_capture.py [--head-dir models/defect-head] [--k 6]

Every annotated customer photo is passed K times through the screen-capture simulation
(tools/capture_sim.py) and through the real server detector at 640x480. Reported per sign:
in how many captures an annotated area was found, and in how many captures of photos without
that sign something was raised.

The saved model was trained on these very photos, so this measures robustness to the capture,
not the ability to recognise new equipment — that is what train_defects.py measures.
"""
import argparse
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.vision import PatchDetector  # noqa: E402
from capture_sim import capture  # noqa: E402

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent


def overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def frame_box(box, to_frame):
    """Bounding box of a photo box in the frame, and the share of it that is inside the frame."""
    xs, ys = zip(*(to_frame(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])))
    full = (min(xs), min(ys), max(xs), max(ys))
    clipped = tuple(min(1.0, max(0.0, v)) for v in full)
    area = (full[2] - full[0]) * (full[3] - full[1])
    visible = (clipped[2] - clipped[0]) * (clipped[3] - clipped[1]) / area if area > 0 else 0.0
    return clipped, visible


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head-dir", default=str(SERVER / "models" / "defect-head"))
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    detector = PatchDetector(head_dir=Path(args.head_dir))
    doc = json.loads((SERVER / "training" / "annotations.json").read_text(encoding="utf-8"))
    totals = {sign: {"found": 0, "shown": 0, "false": 0, "without": 0} for sign in ("leak", "overheat")}
    weakest = {}
    for index, entry in enumerate(doc["images"]):
        photo = ImageOps.exif_transpose(Image.open(ROOT / doc["root"] / entry["file"])).convert("RGB")
        photo.thumbnail((1400, 1400))
        rng = random.Random(args.seed * 100_000 + index)          # the same captures for every model compared
        for _ in range(args.k):
            frame, _, to_frame = capture(photo, rng)
            found = detector.detect(frame)
            for sign, total in totals.items():
                truth = [frame_box(b["box"], to_frame) for b in entry["boxes"] if b["sign"] == sign]
                tolerated = [box for box, _ in truth] + [frame_box(b["box"], to_frame)[0]
                                                         for b in entry["boxes"] if b["sign"] == "ignore"]
                hits = [d for d in found if d.label == sign]
                if truth:
                    if max(visible for _, visible in truth) < 0.5:
                        continue                                   # the area fell out of the frame
                    hit = any(overlaps(d.box, box) for d in hits for box, _ in truth)
                    total["shown"] += 1
                    total["found"] += hit
                    stats = weakest.setdefault(f"{sign}: {entry['file'][-40:]}", [0, 0])
                    stats[0] += hit
                    stats[1] += 1
                else:
                    total["without"] += 1
                    total["false"] += any(not any(overlaps(d.box, box) for box in tolerated) for d in hits)
    print(json.dumps({"head": Path(args.head_dir).name, **totals}, ensure_ascii=False))
    for name, (hit, shown) in sorted(weakest.items(), key=lambda kv: kv[1][0] / kv[1][1])[:6]:
        print(f"   {hit}/{shown}  {name}")


if __name__ == "__main__":
    main()
