"""Download the frozen backbone once, while internet is available.

    .venv/Scripts/python tools/fetch_model.py

After this the server loads everything from server/models/ and never goes online.
DINOv2 (Apache-2.0): https://huggingface.co/facebook/dinov2-small , .../dinov2-base
The trained head (server/models/defect-head) is small and lives in the repository.
"""
import json
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = Path(__file__).resolve().parents[1] / "models"

if __name__ == "__main__":
    meta = MODELS / "defect-head" / "meta.json"
    names = {json.loads(meta.read_text(encoding="utf-8"))["backbone"]} if meta.exists() else {"dinov2-small"}
    for name in sorted(names):
        snapshot_download(f"facebook/{name}", local_dir=MODELS / name,
                          allow_patterns=["config.json", "model.safetensors", "preprocessor_config.json"])
        print(f"Saved {name} to {MODELS / name}")
