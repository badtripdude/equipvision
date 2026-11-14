"""Equipment identification from a camera frame: QR code or the serial number on the nameplate.

QR codes carry "EQV:<equipment id>" (printable from /labels.html). Nameplates are read with
RapidOCR (PP-OCRv4 models bundled in the pip package, Apache-2.0, runs offline on the CPU) and
the text is matched against the serial numbers in the database.
"""
import threading

import numpy as np
from PIL import Image

QR_PREFIX = "EQV:"

# Characters OCR mixes up on stamped plates; both sides are folded before comparing.
_FOLD = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "Z": "2", "S": "5",
                       "B": "8", "G": "6", "—": "-", "–": "-", "_": "-"})


def normalise(text: str) -> str:
    """Upper-case, drop everything except letters, digits and '-', fold look-alike characters."""
    kept = "".join(ch for ch in text.upper().translate(_FOLD) if ch.isalnum() or ch == "-")
    return kept.translate(_FOLD)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def best_distance(serial: str, line: str) -> int:
    """Smallest edit distance between the serial and any window of the line of about its length."""
    if serial in line:
        return 0
    n = len(serial)
    if len(line) <= n:
        return edit_distance(serial, line)
    return min(edit_distance(serial, line[i:i + size])
               for size in (n - 1, n, n + 1) for i in range(0, len(line) - size + 1))


def match_serial(lines: list[str], equipment: list[dict]) -> tuple[dict | None, list[dict]]:
    """(exact match or None, near misses for the engineer to confirm).

    Only an exact serial identifies the unit on its own: neighbouring units differ in one
    character (21031004-3 / 21031004-4), so a one-character OCR slip must not pick for the user.
    """
    folded = [normalise(line) for line in lines if line.strip()]
    # A serial split across OCR lines ("21031004" / "-3") still matches the joined text.
    folded.append("".join(folded))
    exact, near = [], []
    for unit in equipment:
        serial = normalise(unit["serial"] or "")
        if len(serial) < 4:
            continue
        distance = min((best_distance(serial, line) for line in folded), default=len(serial))
        if distance == 0:
            exact.append(unit)
        elif distance == 1 and len(serial) >= 8:
            near.append(unit)
    # A longer serial that contains a shorter one wins ("21031004-1" over a hypothetical "1004-1").
    exact.sort(key=lambda u: -len(u["serial"]))
    if len(exact) == 1 or (len(exact) > 1 and len(exact[0]["serial"]) > len(exact[1]["serial"])):
        return exact[0], []
    return None, exact + near


def match_qr(payload: str, equipment: list[dict]) -> dict | None:
    value = payload.strip()
    if value.upper().startswith(QR_PREFIX):
        value = value[len(QR_PREFIX):]
    for unit in equipment:
        if value in (unit["id"], unit["serial"]):
            return unit
    return None


class FrameReader:
    """QR decoding and OCR; both are loaded on first use so the server starts fast."""

    def __init__(self) -> None:
        self._qr = None
        self._ocr = None
        self._lock = threading.Lock()

    def qr(self, image: Image.Image) -> str | None:
        import cv2

        if self._qr is None:
            self._qr = cv2.QRCodeDetector()
        gray = np.asarray(image.convert("L"))
        text, _, _ = self._qr.detectAndDecode(gray)
        return text or None

    def text(self, image: Image.Image) -> list[str]:
        with self._lock:
            if self._ocr is None:
                from rapidocr_onnxruntime import RapidOCR

                self._ocr = RapidOCR()
            result, _ = self._ocr(np.asarray(image.convert("RGB"))[:, :, ::-1])   # RapidOCR expects BGR
        return [line[1] for line in result or [] if float(line[2]) >= 0.5]


def make_qr(payload: str, scale: int = 10, border: int = 4) -> Image.Image:
    import cv2

    code = cv2.QRCodeEncoder.create().encode(payload)
    code = cv2.resize(code, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    code = cv2.copyMakeBorder(code, *(border * scale,) * 4, cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(code)
