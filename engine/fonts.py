"""Font lookup with graceful fallback: Fredoka -> Baloo -> Poppins -> DejaVu."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from PIL import ImageFont

from . import config as C

PRIORITY_BOLD = ["Fredoka-Bold.ttf", "Baloo2-Bold.ttf", "Poppins-Bold.ttf"]
PRIORITY_MED = ["Poppins-Medium.ttf", "Poppins-Regular.ttf"]
DEJAVU = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _find(names) -> str | None:
    for n in names:
        p = Path(C.FONT_DIR) / n
        if p.exists():
            return str(p)
    for n in DEJAVU:
        if Path(n).exists():
            return n
    return None


def font_path(kind: str = "bold") -> str | None:
    return _find(PRIORITY_BOLD if kind == "bold" else PRIORITY_MED)


@lru_cache(maxsize=64)
def get_font(kind: str, size: int):
    p = font_path(kind)
    if p is None:
        return ImageFont.load_default()
    f = ImageFont.truetype(p, size)
    if "[" in p:                      # variable font (Fredoka) -> Bold instance
        try:
            f.set_variation_by_name("Bold")
        except Exception:
            pass
    return f


def fit_size(text: str, kind: str, max_w: int, start: int, min_size: int = 20) -> int:
    size = start
    while size > min_size:
        f = get_font(kind, size)
        l, t, r, b = f.getbbox(text)
        if r - l <= max_w:
            break
        size -= 2
    return size
