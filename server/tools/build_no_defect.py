"""Render pages of the customer's technical documents as a "nothing to find here" image set.

    .venv/Scripts/python tools/build_no_defect.py

Catalogue pages with photos of new pumps, drawings, tables and text: none of them shows
corrosion, a leak or burn marks, so any detection on them is a false alarm. The split is by
document, so the test part never shares a document with the training part.
Needs `pdftoppm` (poppler) on PATH. Output: datasets/no_defect/{train,test}/*.png
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "Кейс" / "Кейс" / "Документация" / "Паспорта Насосов"
OUT = ROOT / "datasets" / "no_defect"

# document, last page to render
SPLIT = {
    "train": [("HF до 132 м3-час.pdf", 4), ("СБ, уст. чертеж и др..pdf", 8), ("Инструкция по ЭиО.pdf", 10)],
    "test": [("FCR-15-30.pdf", 4), ("Мотор-редукт. РП.pdf", 12), ("РЭ ЭД.pdf", 12)],
}

if __name__ == "__main__":
    tool = shutil.which("pdftoppm")
    if tool is None:
        raise SystemExit("pdftoppm (poppler) is not on PATH")
    for part, documents in SPLIT.items():
        target = OUT / part
        target.mkdir(parents=True, exist_ok=True)
        for index, (name, last) in enumerate(documents):
            subprocess.run([tool, "-png", "-r", "70", "-f", "1", "-l", str(last), str(DOCS / name),
                            str(target / f"doc{index}")], check=True)
        print(part, len(list(target.glob("*.png"))), "pages")
