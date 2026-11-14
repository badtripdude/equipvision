"""Take the needed photos out of the "Multimodal Transformer Defect Recognition Dataset".

    .venv/Scripts/python tools/fetch_transformer.py

Zenodo record 22771088, CC BY 4.0, https://zenodo.org/records/22771088 — photos of power
transformers with YOLO boxes for: cable defect, fire/smoke hazard, insulator crack, oil leak,
rust/corrosion. The archive is 2.8 GB; only the selected photos are read from it (range requests).

Taken: every photo with an oil leak, and a sample of the other classes (they serve as photos
without a leak). In the train split the archive holds several augmented copies of a photo; one
copy per source photo is kept. Output: datasets/transformer/{train,val,test}/ and labels.json.
"""
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zip_remote import member_span, open_remote  # noqa: E402

URL, SIZE = "https://zenodo.org/api/records/22771088/files/data.zip/content", 2836265270
OUT = Path(__file__).resolve().parents[2] / "datasets" / "transformer"
CLASSES = ["cable_defect", "fire_smoke", "insulator_crack", "oil_leak", "rust_corrosion"]
OTHER_PER_SPLIT = {"train": 40, "val": 30, "test": 30}      # per class, photos without an oil leak


def main() -> None:
    archive, raw = open_remote(URL, SIZE)
    infos = {i.filename: i for i in archive.infolist() if not i.is_dir()}
    chosen = {}
    rng = random.Random(0)
    for split in ("train", "val", "test"):
        members = sorted(n for n in infos if n.startswith(f"image/{split}/labels/"))
        raw.preload(min(member_span(infos[n])[0] for n in members), max(member_span(infos[n])[1] for n in members))
        seen_sources, others = set(), {c: [] for c in CLASSES}
        for name in members:
            rows = [line.split() for line in archive.read(name).decode().splitlines() if line.strip()]
            boxes = [{"class": CLASSES[int(r[0])], "box": [float(r[1]) - float(r[3]) / 2, float(r[2]) - float(r[4]) / 2,
                                                           float(r[1]) + float(r[3]) / 2, float(r[2]) + float(r[4]) / 2]}
                     for r in rows]
            stem = name.split("/")[-1][:-4]
            source = stem.split(".rf.")[0]
            if source in seen_sources:
                continue                                  # another augmented copy of a photo already taken
            classes = {b["class"] for b in boxes}
            entry = {"split": split, "source": source, "boxes": boxes}
            if "oil_leak" in classes:
                seen_sources.add(source)
                chosen[stem] = entry
            elif len(classes) == 1:
                others[next(iter(classes))].append((stem, source, entry))
        for cls, pool in others.items():
            rng.shuffle(pool)
            for stem, source, entry in pool:
                if source not in seen_sources and sum(1 for e in chosen.values() if e["split"] == split
                                                      and {b["class"] for b in e["boxes"]} == {cls}) < OTHER_PER_SPLIT[split]:
                    seen_sources.add(source)
                    chosen[stem] = entry

    done = 0
    for stem, entry in chosen.items():
        # Source names are up to 300 characters long, more than a Windows path allows: store under a short name.
        entry["file"] = hashlib.md5(stem.encode()).hexdigest()[:16] + ".jpg"
        target = OUT / entry["split"] / entry["file"]
        long_name = OUT / entry["split"] / f"{stem}.jpg"
        if not target.exists() and len(str(long_name)) < 250 and long_name.exists():
            long_name.rename(target)                      # downloaded by an earlier run
        if not target.exists():
            info = infos[f"image/{entry['split']}/images/{stem}.jpg"]
            raw.preload(*member_span(info))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            raw.chunks = raw.chunks[:1]                   # keep only the index in memory
        done += 1
        if done % 50 == 0:
            print(f"{done}/{len(chosen)}", flush=True)
    (OUT / "labels.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {}
    for entry in chosen.values():
        key = (entry["split"], "oil_leak" if any(b["class"] == "oil_leak" for b in entry["boxes"]) else entry["boxes"][0]["class"])
        summary[key] = summary.get(key, 0) + 1
    print(sorted(summary.items()))


if __name__ == "__main__":
    main()
