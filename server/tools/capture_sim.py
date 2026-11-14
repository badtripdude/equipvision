"""Simulate a phone camera looking at a photo shown on a screen or a poster.

At the defence the camera will point at pictures, not at real equipment, so the model has to
survive perspective, moire, glare, colour cast, blur and JPEG. This is a simulation: it does
not replace a test with a real phone.
"""
import io
import math
import random

import numpy as np
from PIL import Image, ImageFilter

CANVAS = (960, 600)   # the "screen" the photo is shown on


def _homography(src, dst) -> np.ndarray:
    """3x3 matrix H with dst ~ H @ src, from four point pairs."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        rhs += [u, v]
    return np.append(np.linalg.solve(np.array(rows, float), np.array(rhs, float)), 1.0).reshape(3, 3)


def _apply(h: np.ndarray, x, y):
    w = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    return (h[0, 0] * x + h[0, 1] * y + h[0, 2]) / w, (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / w


def capture(photo: Image.Image, rng: random.Random, size=(640, 480)):
    """Returns (frame, to_photo, to_frame).

    to_photo(u, v): frame coordinates 0..1 -> photo coordinates 0..1 (outside 0..1: not on the photo).
    to_frame(x, y): the reverse.
    """
    width, height = size
    cw, ch = CANVAS
    canvas = Image.new("RGB", CANVAS, rng.choice([(8, 8, 10), (30, 30, 34), (240, 240, 240)]))
    scale = min(cw / photo.width, ch / photo.height) * rng.uniform(0.75, 1.0)
    pw, ph = max(1, round(photo.width * scale)), max(1, round(photo.height * scale))
    ox, oy = rng.randint(0, cw - pw), rng.randint(0, ch - ph)
    canvas.paste(photo.resize((pw, ph), Image.BILINEAR), (ox, oy))

    # Where the screen lands in the frame: from "small, far away" to "closer than the frame".
    fill = rng.uniform(0.6, 1.25)
    rw = fill * width
    rh = rw * ch / cw
    if rh > fill * height:
        rh = fill * height
        rw = rh * cw / ch
    x0 = (width - rw) / 2 + rng.uniform(-0.08, 0.08) * width
    y0 = (height - rh) / 2 + rng.uniform(-0.08, 0.08) * height
    quad = [(x + rng.uniform(-0.05, 0.05) * rw, y + rng.uniform(-0.05, 0.05) * rh)
            for x, y in ((x0, y0), (x0 + rw, y0), (x0 + rw, y0 + rh), (x0, y0 + rh))]
    to_canvas = _homography(quad, [(0, 0), (cw, 0), (cw, ch), (0, ch)])      # frame px -> canvas px
    from_canvas = np.linalg.inv(to_canvas)
    coefficients = tuple(to_canvas.flatten()[:8])
    warped = canvas.transform(size, Image.PERSPECTIVE, coefficients, Image.BILINEAR)
    on_screen = Image.new("L", CANVAS, 255).transform(size, Image.PERSPECTIVE, coefficients, Image.BILINEAR)

    noise = np.random.default_rng(rng.getrandbits(32))
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    screen = np.asarray(warped, np.float32) / 255 * rng.uniform(0.75, 1.1)
    if rng.random() < 0.7:                                                    # moire of the pixel grid
        period, angle = rng.uniform(2.5, 9), rng.uniform(0, math.pi)
        wave = np.sin(2 * math.pi * (xx * math.cos(angle) + yy * math.sin(angle)) / period + rng.uniform(0, 6.28))
        screen *= 1 + rng.uniform(0.03, 0.12) * wave[..., None]
    room = np.array([rng.uniform(0.15, 0.75)] * 3, np.float32) * np.array(
        [rng.uniform(0.9, 1.1) for _ in range(3)], np.float32)
    background = room[None, None, :] * (0.75 + 0.5 * yy[..., None] / height)
    background = background + noise.normal(0, 0.03, background.shape).astype(np.float32)
    mask = np.asarray(on_screen, np.float32)[..., None] / 255
    frame = screen * mask + background * (1 - mask)

    if rng.random() < 0.5:                                                    # glare of a lamp
        gx, gy, sigma = rng.uniform(0, width), rng.uniform(0, height), rng.uniform(0.08, 0.3) * width
        frame += rng.uniform(0.1, 0.45) * np.exp(-((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * sigma ** 2))[..., None]
    frame *= np.array([rng.uniform(0.92, 1.08) for _ in range(3)], np.float32)   # white balance
    frame = np.clip(frame, 0, 1) ** rng.uniform(0.8, 1.25)
    frame += noise.normal(0, rng.uniform(0, 0.02), frame.shape).astype(np.float32)
    image = Image.fromarray((np.clip(frame, 0, 1) * 255).astype(np.uint8))
    if rng.random() < 0.5:
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.2)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=rng.randint(55, 85))
    image = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")

    def to_photo(u, v):
        cx, cy = _apply(to_canvas, u * width, v * height)
        return (cx - ox) / pw, (cy - oy) / ph

    def to_frame(x, y):
        fx, fy = _apply(from_canvas, ox + x * pw, oy + y * ph)
        return fx / width, fy / height

    return image, to_photo, to_frame
