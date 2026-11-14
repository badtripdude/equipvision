"""Everyday scenes without defects: rooms, desks, laptops, keyboards, people.

    .venv/Scripts/python tools/fetch_scenes.py

Why: on a phone the camera sees far more than equipment. The first live run marked a black laptop
lid, a red-lit keyboard and a wooden desk as corrosion and leaks — nothing like them was in training.

Source: COCO val2017 (https://cocodataset.org). Only photos whose licence allows reuse without the
non-commercial clause are taken (CC BY 2.0, CC BY-SA 2.0, no known restrictions, US government work),
and only indoor ones: outdoor scenes carry real rust (hydrants, trains, benches) that would be
mislabelled as "nothing". The list of photos comes from the annotation archive, of which only the
needed member is read (HTTP range requests). Nothing here is used at run time.
"""
import json
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zip_remote import AGENT, member_span, open_remote  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "datasets" / "scenes"
ARCHIVE = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
MEMBER = "annotations/instances_val2017.json"
PHOTO = "http://images.cocodataset.org/val2017/{name}"
FREE_LICENSES = {4, 5, 7, 8}       # CC BY, CC BY-SA, no known restrictions, US government work
INDOOR = {"laptop", "keyboard", "mouse", "tv", "cell phone", "remote", "book", "chair", "couch", "bed",
          "dining table", "cup", "bottle", "microwave", "oven", "refrigerator", "sink", "clock", "vase"}
OUTDOOR = {"fire hydrant", "train", "truck", "boat", "bench", "stop sign", "parking meter", "bus", "car",
           "motorcycle", "bicycle", "airplane", "traffic light"}
TRAIN, TEST = 360, 120


def listing() -> dict:
    with urllib.request.urlopen(urllib.request.Request(ARCHIVE, method="HEAD", headers=AGENT), timeout=60) as response:
        size = int(response.headers["Content-Length"])
    archive, raw = open_remote(ARCHIVE, size, index_bytes=200_000)
    info = archive.getinfo(MEMBER)
    raw.preload(*member_span(info))
    return json.loads(archive.read(MEMBER))


def download(name: str, target: Path) -> bool:
    if target.exists():
        return True
    try:
        request = urllib.request.Request(PHOTO.format(name=name), headers=AGENT)
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        target.write_bytes(data)
        return True
    except OSError:
        return False


def main() -> None:
    doc = json.loads((OUT / "listing.json").read_text(encoding="utf-8")) if (OUT / "listing.json").exists() else None
    if doc is None:
        full = listing()
        names = {c["id"]: c["name"] for c in full["categories"]}
        seen: dict[int, set] = {}
        for a in full["annotations"]:
            seen.setdefault(a["image_id"], set()).add(names[a["category_id"]])
        doc = [{"file": im["file_name"], "license": im["license"], "flickr": im.get("flickr_url", "")}
               for im in full["images"]
               if im["license"] in FREE_LICENSES and seen.get(im["id"], set()) & INDOOR
               and not seen.get(im["id"], set()) & OUTDOOR]
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "listing.json").write_text(json.dumps(doc, indent=0), encoding="utf-8")
    print(f"indoor photos with a free licence: {len(doc)}", flush=True)

    random.Random(0).shuffle(doc)
    plan = [("train", e) for e in doc[:TRAIN]] + [("test", e) for e in doc[TRAIN:TRAIN + TEST]]
    for split in ("train", "test"):
        (OUT / split).mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as pool:
        ok = list(pool.map(lambda job: download(job[1]["file"], OUT / job[0] / job[1]["file"]), plan))
    print(f"downloaded {sum(ok)} of {len(plan)} into {OUT}")


if __name__ == "__main__":
    main()
