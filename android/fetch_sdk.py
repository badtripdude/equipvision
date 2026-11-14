"""Download the two Android SDK packages the build needs, straight from Google's repository.

    py android/fetch_sdk.py [sdk_dir]          default: tmp/android-sdk

Takes SDK Build-Tools (aapt2, d8, zipalign, apksigner) and one SDK Platform (android.jar). Names, sizes
and SHA-1 checksums come from Google's own package index; every archive is checked before unpacking.
The packages are covered by the Android SDK Licence Agreement: https://developer.android.com/studio/terms
"""
import hashlib
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://dl.google.com/android/repository/"
INDEX = BASE + "repository2-3.xml"
BUILD_TOOLS = "build-tools;34.0.0"
PLATFORM = "platforms;android-34"
AGENT = {"User-Agent": "sdk-fetch/1.0 (python-urllib)"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=300) as response:
        return response.read()


def archive(index: str, path: str) -> tuple[str, str, int]:
    """(file name, sha1, size) of the package's archive for Windows, or the only one it has."""
    block = re.search(r'<remotePackage path="' + re.escape(path) + r'">(.*?)</remotePackage>', index, re.S)
    if not block:
        raise SystemExit(f"{path} is not in the package index")
    found = []
    for item in re.findall(r"<archive>(.*?)</archive>", block.group(1), re.S):
        host = re.search(r"<host-os>(.*?)</host-os>", item)
        if host and host.group(1) != "windows":
            continue
        found.append((re.search(r"<url>(.*?)</url>", item).group(1),
                      re.search(r"<checksum[^>]*>(.*?)</checksum>", item).group(1),
                      int(re.search(r"<size>(\d+)</size>", item).group(1))))
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one archive, found {len(found)}")
    return found[0]


def place(source: Path, target: Path) -> None:
    """Rename, retried: on Windows an antivirus may hold freshly unpacked files for a few seconds."""
    for attempt in range(10):
        try:
            source.rename(target)
            return
        except PermissionError:
            time.sleep(2 + attempt)
    raise SystemExit(f"could not move {source} to {target}")


def fetch(index: str, path: str, target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        print(f"{path}: already in {target}")
        return
    staging = target.parent / (target.name + "-unpack")
    if staging.exists():                                  # unpacked by an interrupted run
        inner = [item for item in staging.iterdir()]
        place(inner[0] if len(inner) == 1 else staging, target)
        if staging.exists():
            staging.rmdir()
        print(f"{path}: moved to {target}")
        return
    name, sha1, size = archive(index, path)
    print(f"{path}: {name}, {size / 1e6:.0f} MB", flush=True)
    data = get(BASE + name)
    if hashlib.sha1(data).hexdigest() != sha1:
        raise SystemExit(f"{name}: checksum mismatch, nothing unpacked")
    packed = target.parent / name
    packed.parent.mkdir(parents=True, exist_ok=True)
    packed.write_bytes(data)
    with zipfile.ZipFile(packed) as z:
        for member in z.namelist():                       # never write outside the target folder
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise SystemExit(f"unsafe path in {name}: {member}")
        top = {m.split("/")[0] for m in z.namelist()}
        z.extractall(staging)
    packed.unlink()
    place(staging / top.pop() if len(top) == 1 else staging, target)
    if staging.exists():
        staging.rmdir()
    print(f"  unpacked to {target}")


def main() -> None:
    sdk = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "tmp" / "android-sdk")
    index = get(INDEX).decode("utf-8")
    fetch(index, BUILD_TOOLS, sdk / "build-tools" / BUILD_TOOLS.split(";")[1])
    fetch(index, PLATFORM, sdk / "platforms" / PLATFORM.split(";")[1])


if __name__ == "__main__":
    main()
