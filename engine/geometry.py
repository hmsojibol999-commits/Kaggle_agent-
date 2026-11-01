"""Geometry shared by the renderer, the tests and (mirrored in JS) the UI preview.

Coordinates inside the card are in card pixels (0..CARD_W, 0..CARD_H).
Focus points and crop offsets are fractions 0..1.
"""
from __future__ import annotations

import math

from . import config as C


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def cover_scale(iw: int, ih: int) -> float:
    """Scale that makes the image just cover the card."""
    return max(C.CARD_W / iw, C.CARD_H / ih)


def needs_upscale(iw: int, ih: int, zoom: float = 1.0) -> bool:
    """True when the source would be enlarged beyond 1.0x of card size."""
    return cover_scale(iw, ih) * zoom > 1.0 + 1e-9


def base_box(iw: int, ih: int, offset=(0.5, 0.5)) -> tuple:
    """Source-pixel box shown at zoom 1.0. offset is the position inside
    the spare room (0 = left/top aligned, 1 = right/bottom aligned)."""
    s = cover_scale(iw, ih)
    bw, bh = C.CARD_W / s, C.CARD_H / s
    ox = clamp(offset[0], 0.0, 1.0)
    oy = clamp(offset[1], 0.0, 1.0)
    x0 = (iw - bw) * ox
    y0 = (ih - bh) * oy
    return (x0, y0, x0 + bw, y0 + bh)


def view_box(iw: int, ih: int, focus, offset=(0.5, 0.5), zoom: float = 1.0) -> tuple:
    """Zoom about the focus point (kept at the same screen position when the
    image has room), then clamp inside the source so no border shows.
    focus=None (no arrow): zoom about the middle of the visible crop."""
    x0, y0, x1, y1 = base_box(iw, ih, offset)
    if focus is None:
        focus = ((x0 + x1) / 2 / iw, (y0 + y1) / 2 / ih)
    fx, fy = focus[0] * iw, focus[1] * ih
    # keep the focus point's relative position inside the box constant
    rx = (fx - x0) / (x1 - x0)
    ry = (fy - y0) / (y1 - y0)
    rx, ry = clamp(rx, 0.0, 1.0), clamp(ry, 0.0, 1.0)
    bw, bh = (x1 - x0) / zoom, (y1 - y0) / zoom
    nx0 = fx - rx * bw
    ny0 = fy - ry * bh
    nx0 = clamp(nx0, 0.0, iw - bw)
    ny0 = clamp(ny0, 0.0, ih - bh)
    return (nx0, ny0, nx0 + bw, ny0 + bh)


def focus_in_card(box: tuple, focus, iw: int, ih: int) -> tuple:
    """Card-pixel position of the focus point for a given view box."""
    x0, y0, x1, y1 = box
    px = (focus[0] * iw - x0) / (x1 - x0) * C.CARD_W
    py = (focus[1] * ih - y0) / (y1 - y0) * C.CARD_H
    return (clamp(px, 0, C.CARD_W), clamp(py, 0, C.CARD_H))


def card_safe_rect() -> tuple:
    """Part of the card (card pixels) that is also inside the screen safe zone.
    The spec's 900 px card sticks 30 px past x=960, so arrows stay inside
    this rectangle instead."""
    x0 = max(0, C.SAFE_X0 - C.CARD_X)
    y0 = max(0, C.SAFE_Y0 - C.CARD_Y)
    x1 = min(C.CARD_W, C.SAFE_X1 - C.CARD_X)
    y1 = min(C.CARD_H, C.SAFE_Y1 - C.CARD_Y)
    return (x0, y0, x1, y1)


ARROW_GAP = 26        # space between arrow tip and the aimed point
ARROW_MAX = 230
ARROW_MIN = 110
ARROW_MARGIN = 44     # along-axis clearance from the safe edge (absorbs pop overshoot + pulse)
ARROW_INSET = 84      # sideways clearance: head half-width * 1.08 + stroke + safety
ARROW_SW, ARROW_HW, ARROW_HL = 21, 52, 78   # shaft half-width, head half-width, head length
ARROW_MAX_SCALE = 1.08
ARROW_PULSE = 11


def arrow_polygon(tip, tail, scale: float = 1.0) -> list:
    """The 7 polygon points of the block arrow (same shape draw.draw_arrow paints)."""
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    d = math.hypot(dx, dy) or 1e-6
    ux, uy = dx / d, dy / d
    nx, ny = -uy, ux
    L = d * scale
    sw, hw, hl = ARROW_SW * scale, ARROW_HW * scale, min(ARROW_HL * scale, L * 0.7)

    def P(a, b):
        return (tip[0] + ux * a + nx * b, tip[1] + uy * a + ny * b)

    return [P(-L, -sw), P(-hl, -sw), P(-hl, -hw), P(0, 0), P(-hl, hw), P(-hl, sw), P(-L, sw)]


def _poly_inside(pts, rect, pad: float = 6.0) -> bool:
    x0, y0, x1, y1 = rect
    return all(x0 + pad <= x <= x1 - pad and y0 + pad <= y <= y1 - pad for x, y in pts)


def arrow_geometry(px: float, py: float) -> dict:
    """Arrow that comes from the nearest card edge (with enough room) and points
    at (px, py). The aim point is pulled inwards when the focus hugs an edge so the
    whole arrow, including pulse and pop overshoot, stays inside card_safe_rect().

    Returns side, tip, tail (card px) and angle (radians, direction of travel).
    """
    rect = card_safe_rect()
    sx0, sy0, sx1, sy1 = rect
    ax = clamp(px, sx0 + ARROW_INSET, sx1 - ARROW_INSET)
    ay = clamp(py, sy0 + ARROW_INSET, sy1 - ARROW_INSET)
    room = {"left": ax - sx0, "right": sx1 - ax, "top": ay - sy0, "bottom": sy1 - ay}
    order = sorted(room, key=lambda k: room[k])
    need = ARROW_GAP + ARROW_MIN + ARROW_MARGIN
    chosen = next((s for s in order if room[s] >= need), None) or max(room, key=lambda k: room[k])
    length = clamp(room[chosen] - ARROW_GAP - ARROW_MARGIN, 60, ARROW_MAX)
    dirs = {"left": (1, 0), "right": (-1, 0), "top": (0, 1), "bottom": (0, -1)}
    dx, dy = dirs[chosen]
    tilt = math.radians(28)
    if chosen in ("left", "right"):
        sign = 1 if ay < (sy0 + sy1) / 2 else -1
        tilted = math.atan2(dy, dx) + sign * tilt * (1 if chosen == "left" else -1)
    else:
        sign = 1 if ax < (sx0 + sx1) / 2 else -1
        tilted = math.atan2(dy, dx) - sign * tilt * (1 if chosen == "top" else -1)

    def build(ang, ln):
        ux, uy = math.cos(ang), math.sin(ang)
        tip = (ax - ux * ARROW_GAP, ay - uy * ARROW_GAP)
        return tip, (tip[0] - ux * ln, tip[1] - uy * ln)

    for ang in (tilted, math.atan2(dy, dx)):
        ln = length
        while ln >= 60:
            tip, tail = build(ang, ln)
            # check the biggest shape it will ever be drawn at, shifted by the pulse
            ux, uy = math.cos(ang), math.sin(ang)
            ok = True
            for sh in (-ARROW_PULSE, ARROW_PULSE):
                t2 = (tip[0] + ux * sh, tip[1] + uy * sh)
                a2 = (tail[0] + ux * sh, tail[1] + uy * sh)
                ok = ok and _poly_inside(arrow_polygon(t2, a2, ARROW_MAX_SCALE), rect)
            if ok:
                return {"side": chosen, "tip": tip, "tail": tail, "angle": ang, "length": ln}
            ln -= 10
    tip, tail = build(math.atan2(dy, dx), 60)
    return {"side": chosen, "tip": tip, "tail": tail, "angle": math.atan2(dy, dx), "length": 60}
