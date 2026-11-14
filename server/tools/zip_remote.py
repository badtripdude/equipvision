"""Read chosen members of a large remote zip without downloading all of it (HTTP range requests).

Used to take only the needed photos out of a multi-gigabyte dataset archive.
"""
import http.client
import io
import time
import urllib.request
import zipfile
from pathlib import Path

AGENT = {"User-Agent": "EquipVisionDatasetBuilder/0.1 (python-urllib)"}


def fetch_range(url: str, start: int, end: int, piece: int = 400_000) -> bytes:
    """Bytes start..end-1, read in small pieces and retried: long reads break on a poor connection."""
    out = bytearray()
    while start + len(out) < end:
        lo = start + len(out)
        hi = min(end, lo + piece)
        for attempt in range(5):
            try:
                request = urllib.request.Request(url, headers={**AGENT, "Range": f"bytes={lo}-{hi - 1}"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = response.read()
                if len(data) == hi - lo:
                    out += data
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(1 + 2 * attempt)
        else:
            raise OSError(f"could not read bytes {lo}-{hi} of {url}")
    return bytes(out)


class RemoteZip(io.RawIOBase):
    """File-like view of the remote archive, served from byte ranges already downloaded."""

    def __init__(self, url: str, size: int):
        self.url, self.size, self.pos, self.chunks = url, size, 0, []     # chunks: (start, bytes)

    def preload(self, start: int, end: int) -> None:
        self.chunks.append((start, fetch_range(self.url, start, min(end, self.size))))

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, buffer):
        for start, data in self.chunks:
            if start <= self.pos < start + len(data):
                part = data[self.pos - start:self.pos - start + len(buffer)]
                buffer[:len(part)] = part
                self.pos += len(part)
                return len(part)
        want = min(len(buffer), self.size - self.pos)
        if want <= 0:
            return 0
        part = fetch_range(self.url, self.pos, self.pos + want)
        buffer[:len(part)] = part
        self.pos += len(part)
        return len(part)


def open_remote(url: str, size: int, index_bytes: int = 4_000_000) -> tuple[zipfile.ZipFile, RemoteZip]:
    raw = RemoteZip(url, size)
    raw.preload(size - index_bytes, size)                 # the zip index sits at the end
    return zipfile.ZipFile(raw), raw


def member_span(info: zipfile.ZipInfo) -> tuple[int, int]:
    """Byte range that surely covers the member: local header, name, extra field, data."""
    return info.header_offset, info.header_offset + 30 + len(info.filename.encode()) + 512 + info.compress_size
