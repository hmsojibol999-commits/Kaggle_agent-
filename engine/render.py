"""Frame-by-frame renderer. Pure function of the frame index -> deterministic.

Frames are produced by Pillow (supersampled sprites, float-box Lanczos zoom)
across worker processes and piped as raw RGB into ffmpeg (libx264).
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from . import config as C
from . import draw as D
from . import geometry as G
from .fonts import fit_size, get_font
from .timeline import (Timeline, ease_in_cubic, ease_out_back, ease_out_cubic,
                       spring_scale, zoom_at)

HOOK_LINES = ["Can you", "name these?"]
OUTRO_LINES = ["How many", "did you know?"]
OUTRO_SUB = "Comment below!"
WORK_COVER = 0.62          # prepared images are reduced so cover scale ~0.62


@dataclass
class WordRes:
    word: str
    image_path: str        # prepared image (see prep_image)
    focus: tuple
    offset: tuple


# --------------------------------------------------------------- images
def prep_image(src: str | Path, dst: str | Path, max_side: int = 3000) -> dict:
    """Open any image, fix EXIF rotation, convert to RGB, bound the size and
    save a work copy. Returns info about the ORIGINAL (for quality gates)."""
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGB")
    ow, oh = im.size
    s = G.cover_scale(ow, oh)
    # reduce so the card is ~1/WORK_COVER of ... keeps per-frame Lanczos cheap
    target = min(1.0, WORK_COVER / max(s, 1e-6)) if s < WORK_COVER else 1.0
    work = im
    if s < WORK_COVER:
        nw, nh = max(1, int(ow * target)), max(1, int(oh * target))
        # never go below ~1.0x card size at max zoom
        if G.cover_scale(nw, nh) * C.KEN_BURNS_END < 1.0:
            k = 1.0 / (G.cover_scale(ow, oh) * C.KEN_BURNS_END)
            nw, nh = max(1, int(ow * k)), max(1, int(oh * k))
        if max(nw, nh) > max_side:
            k2 = max_side / max(nw, nh)
            nw, nh = int(nw * k2), int(nh * k2)
        work = im.resize((nw, nh), Image.LANCZOS)
    work.save(dst, "PNG", compress_level=1)
    return {"width": ow, "height": oh,
            "upscaled": G.needs_upscale(ow, oh, 1.0),
            "upscaled_at_zoom": G.needs_upscale(ow, oh, C.KEN_BURNS_END),
            "cover_scale": round(s, 4)}


# ----------------------------------------------------------------- scene
class Scene:
    def __init__(self, tl: Timeline, words: list, theme: dict, handle: str):
        self.tl, self.theme, self.handle = tl, theme, handle
        self.words = words
        self.bg = D.build_background(theme)
        self.rays = D.Rays(theme)
        self.title = D.build_title(theme)
        self.shadow = D.card_shadow()
        self.border = D.card_border()
        self.outer = D.rounded_mask((C.CARD_W, C.CARD_H), C.CARD_RADIUS)
        self.halftone = self._halftone_layer()
        self.star = D.star_sprite(72, (255, 255, 255))
        self.star_gold = D.star_sprite(72, (255, 214, 64))
        self.imgs, self.dims = [], []
        for w in words:
            im = Image.open(w.image_path).convert("RGB")
            self.imgs.append(im)
            self.dims.append(im.size)
        n = len(words)
        self.pills = [D.build_pills(theme, i, n) for i in range(n)]
        self.labels = [D.build_label(theme, w.word) for w in words]
        self.tiles = [D.build_tiles(theme, w.word) for w in words]
        self.minis = [self._mini_card(i) for i in range(n)]
        self.hook_sprites = [self._big_text(t) for t in HOOK_LINES]
        self.outro_sprites = [self._big_text(t) for t in OUTRO_LINES]
        self.outro_sub = D.text_sprite(OUTRO_SUB, "bold", 64, D.rgba(theme["accent2"]), pad=10)
        self.handle_pill = self._handle_pill()

    # -- builders
    def _halftone_layer(self) -> Image.Image:
        tile = D.build_halftone(self.theme)
        tw = tile.width // 3           # single tile = 36 px
        w, h = C.W + 2 * tw, 760 + 2 * tw
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for x in range(0, w, tile.width):
            for y in range(0, h, tile.height):
                layer.paste(tile, (x, y))
        # fade out towards the bottom
        a = layer.getchannel("A")
        grad = Image.linear_gradient("L").resize((w, h)).transpose(Image.FLIP_TOP_BOTTOM)
        from PIL import ImageChops
        layer.putalpha(ImageChops.multiply(a, grad))
        return layer

    def _big_text(self, text: str) -> Image.Image:
        size = fit_size(text, "bold", 2 * C.SAFE_HALF_W - 90, 118)
        fill = D.rgba(self.theme["ink"])
        return D.text_sprite(text, "bold", size, fill, stroke_w=0, pad=14)

    def _handle_pill(self) -> Image.Image:
        txt = self.handle
        size = fit_size(txt, "bold", 640, 54)
        sp = D.text_sprite(txt, "bold", size, D.rgba(self.theme["label_fg"]), pad=6)
        w, h = sp.width + 70 * D.SS, sp.height + 26 * D.SS
        w = min(w, int((2 * C.SAFE_HALF_W - 40) * D.SS))
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), h // 2,
                                             fill=D.rgba(self.theme["label_bg"]))
        im.alpha_composite(sp, ((w - sp.width) // 2, (h - sp.height) // 2))
        return im

    def _mini_card(self, i: int) -> Image.Image:
        mw, mh = 260, 289
        box = G.view_box(*self.dims[i], self.words[i].focus, self.words[i].offset, 1.0)
        # crop at the main card aspect, then reduce
        crop = self.imgs[i].resize((mw, mh), Image.LANCZOS, box=box)
        mask = D.rounded_mask((mw, mh), 26)
        out = Image.new("RGBA", (mw + 16, mh + 16), (0, 0, 0, 0))
        wh = Image.new("RGBA", (mw, mh), (255, 255, 255, 255))
        out.paste(wh, (8, 8), mask)
        inner = D.rounded_mask((mw - 10, mh - 10), 21)
        out.paste(crop.resize((mw - 10, mh - 10), Image.LANCZOS), (13, 13), inner)
        return out

    # -- helpers
    def _paste_center(self, f: Image.Image, spr: Image.Image, cx: float, cy: float,
                      scale: float = 1.0, ss: int = 1) -> None:
        w = max(2, int(round(spr.width / ss * scale)))
        h = max(2, int(round(spr.height / ss * scale)))
        s = spr if (w, h) == spr.size else spr.resize((w, h), Image.LANCZOS)
        f.paste(s, (int(round(cx - w / 2)), int(round(cy - h / 2))), s)

    def locate(self, t: float) -> tuple:
        tl = self.tl
        if t >= tl.outro_start:
            return "outro", None
        if t < tl.hook_dur:
            return "hook", None
        for seg in tl.words:
            if seg.start <= t < seg.end:
                return "word", seg
        return "word", tl.words[-1]

    # -- main
    def frame(self, i: int) -> Image.Image:
        t = i / C.FPS
        f = self.bg.copy()
        f.paste(self.rays.color, mask=self.rays.mask(t))
        # drifting halftone dots (float offsets: smooth, no stepping)
        ox, oy = (t * 7.0) % 36, (t * 4.0) % 36
        ht = self.halftone.transform((C.W, 760), Image.AFFINE, (1, 0, ox, 0, 1, oy), Image.BILINEAR)
        f.paste(ht, (0, 0), ht)
        f.paste(self.title, (int(C.CENTER_X - self.title.width / 2), C.TITLE_TEXT_BOX[1] - 4), self.title)
        kind, seg = self.locate(t)
        if kind == "hook":
            self._hook(f, t)
        elif kind == "outro":
            self._outro(f, t - self.tl.outro_start)
        else:
            self._word(f, t, seg)
        return f

    # -- scenes
    def _hook(self, f, t):
        cy = 560
        for k, spr in enumerate(self.hook_sprites):
            u = (t - (0.05 + 0.12 * k)) / 0.38
            if u > 0:
                self._paste_center(f, spr, C.CENTER_X, cy + k * 130, ease_out_back(min(u, 1.0), 1.4), D.SS)
        gap, mw = 25, 276
        total = 3 * 260 + 2 * 25
        x0 = C.CENTER_X - total / 2 + 130
        for k, mc in enumerate(self.minis):
            u = (t - (0.25 + 0.28 * k)) / 0.40
            if u > 0:
                sc = max(0.01, ease_out_back(min(u, 1.0), 1.7))
                self._paste_center(f, mc, x0 + k * (260 + gap), 1000, sc)
                if u > 0.5:
                    b = self._badge(k + 1)
                    self._paste_center(f, b, x0 + k * (260 + gap) - 110, 1000 - 125, 1.0)

    def _badge(self, n: int) -> Image.Image:
        if not hasattr(self, "_badges"):
            self._badges = {}
        if n not in self._badges:
            from PIL import ImageDraw
            ss = 3
            s = 56
            im = Image.new("RGBA", (s * ss, s * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.ellipse((0, 0, s * ss - 1, s * ss - 1), fill=D.rgba(self.theme["label_bg"]),
                      outline=(255, 255, 255, 255), width=5 * ss)
            ft = get_font("bold", 34 * ss)
            bb = d.textbbox((0, 0), str(n), font=ft)
            d.text(((s * ss - (bb[2] - bb[0])) / 2 - bb[0], (s * ss - (bb[3] - bb[1])) / 2 - bb[1]),
                   str(n), font=ft, fill=D.rgba(self.theme["label_fg"]))
            self._badges[n] = im.resize((s, s), Image.LANCZOS)
        return self._badges[n]

    def _outro(self, f, t):
        for k, spr in enumerate(self.outro_sprites):
            u = (t - (0.05 + 0.14 * k)) / 0.40
            if u > 0:
                self._paste_center(f, spr, C.CENTER_X, 600 + k * 135, ease_out_back(min(u, 1.0), 1.4), D.SS)
        u = (t - 0.55) / 0.40
        if u > 0:
            self._paste_center(f, self.outro_sub, C.CENTER_X, 930, ease_out_back(min(u, 1.0), 1.4), D.SS)
        u = (t - 0.90) / 0.40
        if u > 0:
            self._paste_center(f, self.handle_pill, C.CENTER_X, 1080,
                               ease_out_back(min(u, 1.0), 1.4), D.SS)

    def _word(self, f, t, seg):
        r = t - seg.start
        i = seg.index
        w = self.words[i]
        iw, ih = self.dims[i]
        # slide in / out offsets
        out_start = seg.duration - C.TRANSITION_SEC
        if r < C.SLIDE_IN_SEC:
            u = r / C.SLIDE_IN_SEC
            xoff = (1.0 - ease_out_back(u, 1.3)) * 760
        elif r > out_start:
            u = (r - out_start) / C.TRANSITION_SEC
            xoff = -ease_in_cubic(u) * 1200
        else:
            xoff = 0.0
        cx0 = C.CARD_X + xoff
        # pills
        pill = self.pills[i]
        f.paste(pill, (int(C.CENTER_X - pill.width / 2 + xoff * 0.15), C.PILL_BOX[1]), pill)
        # card
        zoom = zoom_at(r, seg.duration)
        box = G.view_box(iw, ih, w.focus, w.offset, zoom)
        img = self.imgs[i].resize((C.CARD_W, C.CARD_H), Image.LANCZOS, box=box)
        f.paste(self.shadow, (int(round(cx0)) - 70, C.CARD_Y - 70), self.shadow)
        f.paste(img, (int(round(cx0)), C.CARD_Y), self.outer)
        f.paste(self.border, (int(round(cx0)), C.CARD_Y), self.border)
        # arrow
        a_in = C.SLIDE_IN_SEC + C.ARROW_DELAY
        a_out = seg.reveal + 0.25
        if a_in <= r < a_out and abs(xoff) < 1.0:
            px, py = G.focus_in_card(box, w.focus, iw, ih)
            geo = G.arrow_geometry(px, py)
            pulse = math.sin(2 * math.pi * 1.7 * (r - a_in))
            ux, uy = math.cos(geo["angle"]), math.sin(geo["angle"])
            shift = G.ARROW_PULSE * pulse
            tip = (geo["tip"][0] + ux * shift + C.CARD_X, geo["tip"][1] + uy * shift + C.CARD_Y)
            tail = (geo["tail"][0] + ux * shift + C.CARD_X, geo["tail"][1] + uy * shift + C.CARD_Y)
            sc = ease_out_back(min(1.0, (r - a_in) / 0.22), 1.6) * (1.0 + 0.03 * pulse)
            alpha = 1.0 if r < seg.reveal else max(0.0, 1.0 - (r - seg.reveal) / 0.25)
            if sc > 0.02:
                ar, pos = D.draw_arrow(tip, tail, min(sc, G.ARROW_MAX_SCALE), alpha)
                f.paste(ar, pos, ar)
        # ring (countdown)
        if seg.cd_start <= r < seg.reveal + 0.15 and abs(xoff) < 1.0:
            if r < seg.reveal:
                k = r - seg.cd_start
                number = 2 if k < 1.0 else 1
                frac = k - (0 if number == 2 else 1.0)
                sweep = 1.0 - frac
                pop = ease_out_back(min(1.0, k / 0.25), 1.8) if k < 0.25 else 1.0
                # tiny number "bump" at each change
                if frac < 0.15:
                    pop *= 1.0 + 0.10 * math.sin(math.pi * frac / 0.15)
                ring = D.draw_ring(self.theme, sweep, number, pop)
            else:
                u = (r - seg.reveal) / 0.15
                ring = D.draw_ring(self.theme, 0.0, 1, max(0.05, 1.0 - u))
            f.paste(ring, (C.RING_CENTER[0] - ring.width // 2, C.RING_CENTER[1] - ring.height // 2), ring)
        # label + spelling + sparkles
        if r >= seg.reveal:
            tr = r - seg.reveal
            lab = self.labels[i]
            sc = spring_scale(tr, 0.40)
            lx = C.CENTER_X + xoff
            self._paste_center(f, lab, lx, 1422, sc, D.SS)
            tiles, total, ts = self.tiles[i]
            x0 = lx - total / 2
            for k, (spr, x) in enumerate(tiles):
                t0 = seg.spell_start + k * seg.spell_step
                if r >= t0:
                    u = (r - t0) / 0.18
                    s = max(0.05, ease_out_back(min(u, 1.0), 2.2))
                    self._paste_center(f, spr, x0 + x + ts / 2, (C.SPELL_Y[0] + C.SPELL_Y[1]) / 2, s, D.SS)
            if tr < 0.95:
                self._sparkles(f, tr, lx, 1422, lab.width / D.SS)

    def _sparkles(self, f, tr, cx, cy, label_w):
        import random
        rnd = random.Random(1234 + 17)
        for k in range(10):
            ang = rnd.uniform(0, 2 * math.pi)
            rad_x = label_w / 2 * rnd.uniform(0.55, 1.05)
            rad_y = rnd.uniform(50, 105)
            delay = rnd.uniform(0.0, 0.35)
            life = rnd.uniform(0.45, 0.65)
            size = rnd.uniform(0.6, 1.1)
            u = (tr - delay) / life
            if 0 < u < 1:
                s = math.sin(math.pi * u) * size
                px = cx + math.cos(ang) * rad_x
                py = cy + math.sin(ang) * rad_y
                spr = self.star_gold if k % 2 else self.star
                rot = spr.rotate(u * 90, Image.BICUBIC)
                self._paste_center(f, rot, px, py, max(0.05, s))


# --------------------------------------------------------------- pipeline
_SCENE: Scene | None = None


def _init_worker(tl, words, theme, handle):
    global _SCENE
    _SCENE = Scene(tl, words, theme, handle)


def _render_index(i: int) -> bytes:
    return _SCENE.frame(i).tobytes()          # type: ignore[union-attr]


def encode_cmd(audio_wav: str, out_path: str, preset: str = C.PRESET, crf: int = C.CRF) -> list:
    return [
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-", "-i", audio_wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
        "-level", "4.2", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", C.AUDIO_BITRATE, "-ar", str(C.AUDIO_RATE), "-ac", "2",
        out_path,
    ]


def render_video(tl: Timeline, words: list, theme: dict, handle: str, audio_wav: str,
                 out_path: str, workers: int | None = None,
                 preset: str | None = None, crf: int | None = None,
                 progress=None) -> None:
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    preset = preset or os.environ.get("WORDCLIP_PRESET", C.PRESET)
    crf = crf or int(os.environ.get("WORDCLIP_CRF", C.CRF))
    n = tl.frames()
    proc = subprocess.Popen(encode_cmd(audio_wav, out_path, preset, crf),
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        if workers == 1:
            scene = Scene(tl, words, theme, handle)
            for i in range(n):
                proc.stdin.write(scene.frame(i).tobytes())
                if progress and i % 60 == 0:
                    progress(i, n)
        else:
            ctx = mp.get_context("fork")
            with ctx.Pool(workers, initializer=_init_worker,
                          initargs=(tl, words, theme, handle)) as pool:
                for i, raw in enumerate(pool.imap(_render_index, range(n), chunksize=4)):
                    proc.stdin.write(raw)
                    if progress and i % 60 == 0:
                        progress(i, n)
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed ({rc}): {err[-600:]}")
    except BrokenPipeError:
        err = proc.stderr.read().decode(errors="ignore")
        raise RuntimeError(f"ffmpeg closed the pipe: {err[-600:]}")
    finally:
        if proc.poll() is None:
            proc.kill()


def save_frames(tl: Timeline, words: list, theme: dict, handle: str, times: list,
                out_dir: str) -> list:
    """Render specific timestamps to PNG (fast visual review)."""
    scene = Scene(tl, words, theme, handle)
    paths = []
    for t in times:
        i = min(tl.frames() - 1, max(0, int(round(t * C.FPS))))
        p = str(Path(out_dir) / f"frame_{t:06.2f}.png")
        scene.frame(i).save(p)
        paths.append(p)
    return paths
