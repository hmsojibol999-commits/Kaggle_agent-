"""Mock mode helpers: gradient 'photos' with an obvious object (no network)."""
from __future__ import annotations

import colorsys
import random

import numpy as np
from PIL import Image, ImageDraw

from .fonts import get_font


def mock_photo(photo_id: int, word: str, size=(2400, 1800)) -> Image.Image:
    rnd = random.Random(photo_id)
    w, h = size
    hue = rnd.random()
    c1 = np.array(colorsys.hsv_to_rgb(hue, 0.45, 0.95)) * 255
    c2 = np.array(colorsys.hsv_to_rgb((hue + 0.15) % 1, 0.65, 0.55)) * 255
    y = np.linspace(0, 1, h)[:, None, None]
    x = np.linspace(0, 1, w)[None, :, None]
    arr = c1 * (1 - (0.6 * y + 0.4 * x)) + c2 * (0.6 * y + 0.4 * x)
    im = Image.fromarray(arr.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im)
    # the "object": an off-centre rounded block with a face so focus is checkable
    ox, oy = int(w * rnd.uniform(0.35, 0.65)), int(h * rnd.uniform(0.4, 0.6))
    r = int(min(w, h) * 0.17)
    d.rounded_rectangle((ox - r, oy - r, ox + r, oy + r), r // 4, fill=(250, 250, 250),
                        outline=(30, 30, 30), width=max(6, r // 20))
    d.ellipse((ox - r // 2, oy - r // 2, ox + r // 2, oy + r // 2), fill=(230, 60, 60))
    f = get_font("bold", int(h * 0.07))
    d.text((int(w * 0.04), int(h * 0.03)), f"MOCK {word}", font=f, fill=(255, 255, 255))
    im.info["focus"] = (ox / w, oy / h)
    return im


def mock_focus(photo_id: int) -> tuple:
    """Position (fractions) of the mock photo's object, so tests can aim at it."""
    rnd = random.Random(photo_id)
    rnd.random()                       # hue, same call order as mock_photo
    return (rnd.uniform(0.35, 0.65), rnd.uniform(0.4, 0.6))
