"""Drawing primitives. Everything is drawn supersampled then downsampled
with Lanczos so edges and text are anti-aliased and crisp.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C
from .config import hex_to_rgb
from .fonts import fit_size, get_font

SS = C.SUPERSAMPLE


# ------------------------------------------------------------------ utils
def rgba(hexcol: str, a: int = 255) -> tuple:
    r, g, b = hex_to_rgb(hexcol)
    return (r, g, b, a)


def rounded_mask(size: tuple, radius: int, ss: int = 4) -> Image.Image:
    """Anti-aliased rounded-rectangle alpha mask ('L')."""
    w, h = size
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def downsample(img: Image.Image, ss: int) -> Image.Image:
    if ss == 1:
        return img
    return img.resize((img.width // ss, img.height // ss), Image.LANCZOS)


def text_sprite(text: str, kind: str, size: int, fill, stroke_w: int = 0,
                stroke_fill=None, pad: int = 12, ss: int = SS) -> Image.Image:
    """RGBA text image rendered at ss x (caller scales down)."""
    f = get_font(kind, size * ss)
    l, t, r, b = f.getbbox(text, stroke_width=stroke_w * ss)
    w, h = (r - l) + 2 * pad * ss, (b - t) + 2 * pad * ss
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad * ss - l, pad * ss - t), text, font=f, fill=fill,
                            stroke_width=stroke_w * ss, stroke_fill=stroke_fill)
    return im


def to_1x(sprite_ss: Image.Image, ss: int = SS) -> Image.Image:
    return downsample(sprite_ss, ss)


# ------------------------------------------------------------- background
def _brush_corner(theme: dict, flip: bool, seed: int) -> Image.Image:
    """Ragged diagonal brush stroke in a corner, drawn at 1x*SS then reduced."""
    rnd = random.Random(seed)
    W, H = C.W * SS, C.H * SS
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    acc = rgba(theme["accent"])
    light = rgba("#FFFFFF", 200)
    L = 400 * SS
    # base diagonal band
    d.polygon([(0, 0), (L, 0), (0, int(L * 1.0))], fill=acc)
    # ragged streaks along the diagonal edge
    for i in range(40):
        t = i / 39.0
        x = L * (1 - t)
        y = L * 1.0 * t
        ln = rnd.uniform(24, 95) * SS
        wd = rnd.uniform(4, 13) * SS
        ang = math.radians(rnd.uniform(-8, 8))
        dx, dy = math.cos(math.radians(45) + ang), math.sin(math.radians(45) + ang)
        col = acc if rnd.random() < 0.72 else light
        d.line([(x, y), (x + dx * ln, y + dy * ln)], fill=col, width=int(wd))
    # darker inner streaks
    dark = rgba(theme["accent2"], 70)
    for i in range(8):
        t = rnd.uniform(0.05, 0.6)
        x, y = L * 0.5 * (1 - t), L * 0.5 * t
        d.line([(x, y), (x + 70 * SS, y + 70 * SS)], fill=dark, width=int(rnd.uniform(3, 7) * SS))
    im = im.resize((C.W, C.H), Image.LANCZOS)
    if flip:
        im = im.transpose(Image.ROTATE_180)
    return im


def _decor(theme: dict) -> Image.Image:
    """Dot grids, x marks, zig-zags and triangles like the reference style."""
    ink = rgba(theme["ink"], 230)
    im = Image.new("RGBA", (C.W * SS, C.H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def dots(x, y, n=4, gap=16, r=4):
        for i in range(n):
            for j in range(n):
                cx, cy = (x + i * gap) * SS, (y + j * gap) * SS
                d.ellipse((cx - r * SS, cy - r * SS, cx + r * SS, cy + r * SS), fill=ink)

    def cross(x, y, s=9):
        d.line([((x - s) * SS, (y - s) * SS), ((x + s) * SS, (y + s) * SS)], fill=ink, width=3 * SS)
        d.line([((x - s) * SS, (y + s) * SS), ((x + s) * SS, (y - s) * SS)], fill=ink, width=3 * SS)

    def zigzag(x, y, n=4, w=14, h=10):
        pts = [((x + i * w) * SS, (y + (h if i % 2 else 0)) * SS) for i in range(n)]
        d.line(pts, fill=ink, width=3 * SS, joint="curve")

    def tri(x, y, s, rot):
        pts = []
        for k in range(3):
            a = math.radians(rot + k * 120)
            pts.append(((x + math.cos(a) * s) * SS, (y + math.sin(a) * s) * SS))
        d.polygon(pts, fill=rgba(theme["accent"]), outline=ink)
        d.line(pts + [pts[0]], fill=ink, width=3 * SS, joint="curve")

    dots(930, 90)
    dots(70, 1700)
    dots(930, 1700)
    cross(1010, 330)
    cross(60, 590)
    cross(1010, 1330)
    cross(70, 1560)
    zigzag(960, 520)
    zigzag(50, 1180, n=5)
    tri(1000, 410, 26, 0)
    tri(60, 1290, 26, 180)
    return im.resize((C.W, C.H), Image.LANCZOS)


def build_background(theme: dict, seed: int = 3) -> Image.Image:
    """Static RGB background: gradient, brush corners and decor."""
    y, x = np.mgrid[0:C.H, 0:C.W].astype(np.float32)
    cx, cy = C.W / 2, C.H * 0.55
    dist = np.sqrt(((x - cx) / C.W) ** 2 + ((y - cy) / C.H) ** 2) / 0.62
    dist = np.clip(dist, 0, 1)[..., None]
    c1 = np.array(hex_to_rgb(theme["bg1"]), dtype=np.float32)
    c2 = np.array(hex_to_rgb(theme["bg2"]), dtype=np.float32)
    arr = c1 * (1 - dist) + c2 * dist
    base = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
    base.alpha_composite(_brush_corner(theme, False, seed))
    base.alpha_composite(_brush_corner(theme, True, seed + 1))
    base.alpha_composite(_decor(theme))
    return base.convert("RGB")


class Rays:
    """Cheap animated light rays: low-res numpy angle field, upscaled per frame."""

    def __init__(self, theme: dict, down: int = 6):
        self.down = down
        h, w = C.H // down, C.W // down
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        ox, oy = (C.W / 2) / down, (C.H * 0.52) / down
        self.theta = np.arctan2(y - oy, x - ox)
        self.dist = np.sqrt((x - ox) ** 2 + (y - oy) ** 2)
        b = np.array(hex_to_rgb(theme["bg2"]), dtype=np.float32)
        self.color = tuple(int(v) for v in (b * 0.86))
        self.fade = np.clip(self.dist / (0.9 * (C.H / down) * 0.5), 0.15, 1.0)

    def mask(self, t: float) -> Image.Image:
        phase = 0.06 * t
        s = np.sin(20.0 * self.theta + phase)
        m = np.clip(s * 1.6, -1, 1) * 0.5 + 0.5
        m = (m * self.fade * 0.32 * 255).astype(np.uint8)
        return Image.fromarray(m, "L").resize((C.W, C.H), Image.BILINEAR)


def build_halftone(theme: dict) -> Image.Image:
    """Seamless halftone dot tile, 3x3 tiles, used as a slowly drifting layer."""
    tile = 36
    n = 3
    im = Image.new("RGBA", (tile * n * SS, tile * n * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = rgba(theme["ink"], 38)
    for i in range(n):
        for j in range(n):
            cx, cy = (i * tile + tile / 2) * SS, (j * tile + tile / 2) * SS
            r = 3.2 * SS
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    return im.resize((tile * n, tile * n), Image.LANCZOS)


# ------------------------------------------------------------------ title
def build_title(theme: dict) -> Image.Image:
    """'WHAT'S THIS CALLED?' with a chunky extruded look. Returns 1x RGBA."""
    text = "WHAT\u2019S THIS CALLED?"
    max_w = 2 * C.SAFE_HALF_W - 40
    size = fit_size(text, "bold", max_w * SS // SS - 24, 86)
    f = get_font("bold", size * SS)
    l, t, r, b = f.getbbox(text, stroke_width=7 * SS)
    pad = 20 * SS
    w, h = (r - l) + 2 * pad, (b - t) + 2 * pad + 14 * SS
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    edge = rgba(theme["title_edge"])
    fill = rgba(theme["title_fill"])
    for k in range(12, 0, -1):         # extrusion (depth)
        d.text((pad - l, pad - t + k * SS * 0.8), text, font=f, fill=edge,
               stroke_width=7 * SS, stroke_fill=edge)
    d.text((pad - l, pad - t), text, font=f, fill=fill, stroke_width=7 * SS, stroke_fill=edge)
    # thin highlight
    d.text((pad - l, pad - t - 2 * SS), text, font=f, fill=rgba("#FFFFFF", 70))
    return im.resize((w // SS, h // SS), Image.LANCZOS)


# ----------------------------------------------------------------- pills
def build_pills(theme: dict, index: int, total: int) -> Image.Image:
    """Progress pills; the current one is wider and carries 'n/total'."""
    ss = 3
    h = 34
    dot_w, cur_w, gap = 34, 96, 10
    widths = [cur_w if i == index else dot_w for i in range(total)]
    tw = sum(widths) + gap * (total - 1)
    im = Image.new("RGBA", (tw * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 0
    for i, wd in enumerate(widths):
        cur = i == index
        d.rounded_rectangle((x * ss, 0, (x + wd) * ss - 1, h * ss - 1), h * ss // 2,
                            fill=rgba(theme["label_bg"] if cur else theme["ink"], 255 if cur else 70))
        if cur:
            f = get_font("bold", 22 * ss)
            txt = f"{index + 1}/{total}"
            bb = d.textbbox((0, 0), txt, font=f)
            d.text((x * ss + (wd * ss - (bb[2] - bb[0])) / 2 - bb[0],
                    (h * ss - (bb[3] - bb[1])) / 2 - bb[1]), txt, font=f,
                   fill=rgba(theme["label_fg"]))
        x += wd + gap
    return im.resize((tw, h), Image.LANCZOS)


# ------------------------------------------------------------------ card
def card_shadow() -> Image.Image:
    pad = 70
    sh = Image.new("RGBA", (C.CARD_W + 2 * pad, C.CARD_H + 2 * pad), (0, 0, 0, 0))
    m = Image.new("L", sh.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad + 16, pad + C.CARD_W, pad + 16 + C.CARD_H),
                                        C.CARD_RADIUS, fill=110)
    m = m.filter(ImageFilter.GaussianBlur(24))
    sh.putalpha(m)
    return sh


def card_border() -> Image.Image:
    """White ring (thin border) on top of the image, RGBA."""
    outer = rounded_mask((C.CARD_W, C.CARD_H), C.CARD_RADIUS)
    inner = Image.new("L", (C.CARD_W, C.CARD_H), 0)
    inner.paste(rounded_mask((C.CARD_W - 12, C.CARD_H - 12), C.CARD_RADIUS - 6), (6, 6))
    ring = Image.new("L", (C.CARD_W, C.CARD_H), 0)
    ring_arr = np.clip(np.asarray(outer, dtype=np.int16) - np.asarray(inner, dtype=np.int16), 0, 255)
    ring = Image.fromarray(ring_arr.astype(np.uint8), "L")
    im = Image.new("RGBA", (C.CARD_W, C.CARD_H), (255, 255, 255, 0))
    im.putalpha(ring)
    return im


# ----------------------------------------------------------------- arrow
def draw_arrow(tip: tuple, tail: tuple, scale: float = 1.0, alpha: float = 1.0) -> tuple:
    """Green block arrow from tail to tip (frame coordinates).
    Returns (RGBA image at 1x, (x, y) top-left)."""
    from .geometry import arrow_polygon
    ss = 3
    pts = arrow_polygon(tip, tail, scale)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = 26
    x0, y0 = int(min(xs)) - pad, int(min(ys)) - pad
    wpx, hpx = int(max(xs) - min(xs)) + 2 * pad, int(max(ys) - min(ys)) + 2 * pad
    im = Image.new("RGBA", (wpx * ss, hpx * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    loc = [((px - x0) * ss, (py - y0) * ss) for px, py in pts]
    shadow = [(x + 5 * ss, y + 8 * ss) for x, y in loc]
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(shadow, fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(4 * ss))
    im.alpha_composite(sh)
    d = ImageDraw.Draw(im)
    d.line(loc + [loc[0]], fill=(255, 255, 255, 255), width=int(24 * ss), joint="curve")
    d.line(loc + [loc[0]], fill=(10, 60, 35, 255), width=int(12 * ss), joint="curve")
    d.polygon(loc, fill=(34, 226, 122, 255))
    # highlight stripe along the shaft
    p0, p1 = pts[0], pts[1]
    q0, q1 = pts[6], pts[5]
    # highlight stripe along the upper third of the shaft
    h0 = (p0[0] * 0.7 + q0[0] * 0.3, p0[1] * 0.7 + q0[1] * 0.3)
    h1 = (p1[0] * 0.7 + q1[0] * 0.3, p1[1] * 0.7 + q1[1] * 0.3)
    k = 0.12
    h0 = (h0[0] + (h1[0] - h0[0]) * k, h0[1] + (h1[1] - h0[1]) * k)
    d.line([((h0[0] - x0) * ss, (h0[1] - y0) * ss), ((h1[0] - x0) * ss, (h1[1] - y0) * ss)],
           fill=(190, 255, 215, 255), width=int(6 * ss))
    out = im.resize((wpx, hpx), Image.LANCZOS)
    if alpha < 1.0:
        a = out.getchannel("A").point(lambda v: int(v * alpha))
        out.putalpha(a)
    return out, (x0, y0)


# ------------------------------------------------------------------ ring
def draw_ring(theme: dict, sweep: float, number: int, pop: float = 1.0) -> Image.Image:
    """Countdown ring: black disc, gradient arc that sweeps away, big number.
    Returns RGBA centred square image at 1x."""
    ss = 3
    R = C.RING_RADIUS
    out = C.RING_OUTLINE
    size = (R + out + 6) * 2
    im = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size * ss / 2
    r_out = (R + out) * ss
    d.ellipse((c - r_out, c - r_out, c + r_out, c + r_out), fill=(255, 255, 255, 255))
    r = R * ss
    d.ellipse((c - r, c - r, c + r, c + r), fill=(12, 12, 14, 255))
    a, b = hex_to_rgb(theme["ring_a"]), hex_to_rgb(theme["ring_b"])
    sweep = max(0.0, min(1.0, sweep))
    segs = 90
    n_on = int(round(segs * sweep))
    ring_w = int(22 * ss)
    rr = r - ring_w // 2 - 10 * ss
    for i in range(n_on):
        k = i / max(segs - 1, 1)
        col = tuple(int(a[j] * (1 - k) + b[j] * k) for j in range(3)) + (255,)
        a0 = -90 + i * 360 / segs
        d.arc((c - rr, c - rr, c + rr, c + rr), a0 - 0.2, a0 + 360 / segs + 0.9, fill=col,
              width=ring_w)
    f = get_font("bold", int(96 * ss))
    txt = str(number)
    bb = d.textbbox((0, 0), txt, font=f)
    d.text((c - (bb[2] - bb[0]) / 2 - bb[0], c - (bb[3] - bb[1]) / 2 - bb[1] + 4 * ss), txt,
           font=f, fill=(255, 255, 255, 255))
    out_im = im.resize((size, size), Image.LANCZOS)
    if abs(pop - 1.0) > 1e-3:
        s = max(0.05, pop)
        out_im = out_im.resize((max(2, int(size * s)), max(2, int(size * s))), Image.LANCZOS)
    return out_im


# ----------------------------------------------------------------- label
def build_label(theme: dict, word: str) -> Image.Image:
    """Answer label sprite at SS x. Width capped so the 1.08 pop stays safe."""
    max_label_w = int(2 * C.SAFE_HALF_W / C.LABEL_POP_MAX) - 8     # ~770
    pad_x = 44
    text = word.title()
    size = fit_size(text, "bold", max_label_w - 2 * pad_x, 92)
    f = get_font("bold", size * SS)
    l, t, r, b = f.getbbox(text)
    tw, th = (r - l), (b - t)
    w = max(360 * SS, tw + 2 * pad_x * SS)
    h = 104 * SS
    margin = 10 * SS
    im = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((margin, margin, margin + w, margin + h), 24 * SS,
                        fill=rgba(theme["label_bg"]), outline=rgba(theme["ink"]), width=5 * SS)
    d.text((margin + (w - tw) / 2 - l, margin + (h - th) / 2 - t), text, font=f,
           fill=rgba(theme["label_fg"]))
    return im


def build_tiles(theme: dict, word: str) -> tuple:
    """Letter tiles for the spelling row. Returns (list of (sprite_ss, x_offset_1x), total_w)."""
    letters = [c for c in word.upper()]
    n_letters = sum(1 for c in letters if c != " ")
    gap = 8
    max_w = 2 * C.SAFE_HALF_W - 40
    space_w = 0.5
    ts = int(min(60, (max_w - gap * (len(letters) - 1)) / (n_letters + space_w * (len(letters) - n_letters))))
    ts = max(30, ts)
    total = 0.0
    xs = []
    for ch in letters:
        if ch == " ":
            total += ts * space_w + gap
            xs.append(None)
        else:
            xs.append(total)
            total += ts + gap
    total -= gap
    sprites = []
    for ch, x in zip(letters, xs):
        if x is None:
            continue
        im = Image.new("RGBA", (ts * SS, ts * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, ts * SS - 1, ts * SS - 1), 12 * SS, fill=(255, 255, 255, 255),
                            outline=rgba(theme["label_bg"]), width=5 * SS)
        f = get_font("bold", int(ts * 0.62) * SS)
        bb = d.textbbox((0, 0), ch, font=f)
        d.text(((ts * SS - (bb[2] - bb[0])) / 2 - bb[0], (ts * SS - (bb[3] - bb[1])) / 2 - bb[1]),
               ch, font=f, fill=rgba(theme["ink"]))
        sprites.append((im, x))
    return sprites, total, ts


# -------------------------------------------------------------- sparkles
def star_sprite(size: int, color=(255, 255, 255)) -> Image.Image:
    ss = 4
    im = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size * ss / 2
    pts = []
    for k in range(8):
        ang = math.radians(k * 45 - 90)
        rad = c * (0.98 if k % 2 == 0 else 0.26)
        pts.append((c + math.cos(ang) * rad, c + math.sin(ang) * rad))
    d.polygon(pts, fill=color + (255,))
    return im.resize((size, size), Image.LANCZOS)
