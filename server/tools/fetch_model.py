"""Download the models once, while internet is available.

    .venv/Scripts/python tools/fetch_model.py

After this the server loads everything from server/models/ and never goes online.
  DINOv2 (Apache-2.0), the frozen backbone of the defect model: https://huggingface.co/facebook/dinov2-base
  Vosk small Russian model (Apache-2.0), for choosing equipment by voice: https://alphacephei.com/vosk/models
The trained head (server/models/defect-head) is small and lives in the repository.
"""
import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = Path(__file__).resolve().parents[1] / "models"
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"


def fetch_speech_model() -> None:
    target = MODELS / "vosk-ru"
    if (target / "am").exists():
        print(f"Speech model already in {target}")
        return
    with urllib.request.urlopen(VOSK_URL, timeout=300) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    for name in archive.namelist():                       # never write outside the unpack folder
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise SystemExit(f"Unsafe path in the archive: {name}")
    unpack = MODELS / "vosk-ru-unpack"
    shutil.rmtree(unpack, ignore_errors=True)
    archive.extractall(unpack)
    (unpack / "vosk-model-small-ru-0.22").rename(target)
    unpack.rmdir()
    print(f"Saved the speech model to {target}")


if __name__ == "__main__":
    meta = MODELS / "defect-head" / "meta.json"
    names = {json.loads(meta.read_text(encoding="utf-8"))["backbone"]} if meta.exists() else {"dinov2-base"}
    for name in sorted(names):
        snapshot_download(f"facebook/{name}", local_dir=MODELS / name,
                          allow_patterns=["config.json", "model.safetensors", "preprocessor_config.json"])
        print(f"Saved {name} to {MODELS / name}")
    fetch_speech_model()
