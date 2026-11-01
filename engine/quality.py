"""Quality gates. build.py fails loudly if any check fails."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
import numpy as np

from . import config as C
from . import errors as E
from . import media
from .words import normalize


def _ok(name: str, ok: bool, detail: str, hard: bool = True) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "hard": hard}


def _fps(stream: dict) -> float:
    num, _, den = stream.get("r_frame_rate", "0/1").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def duration_range(n_words: int) -> tuple:
    if n_words == 3:
        return C.DURATION_RANGE
    return (12.0, 60.0)       # custom word counts: relaxed range


def check_video(path: str | Path, n_words: int, words: list, image_infos: list,
                expected_total: float) -> list:
    """Return the list of check results (see _ok)."""
    res = []
    info = media.probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    res.append(_ok("video codec h264", v.get("codec_name") == "h264", str(v.get("codec_name"))))
    res.append(_ok("profile High", v.get("profile") == "High", str(v.get("profile"))))
    res.append(_ok("pixel format yuv420p", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt"))))
    res.append(_ok("resolution 1080x1920", (v.get("width"), v.get("height")) == (C.W, C.H),
                   f"{v.get('width')}x{v.get('height')}"))
    res.append(_ok("30 fps", abs(_fps(v) - C.FPS) < 0.01, f"{_fps(v):.3f}"))
    cs = v.get("color_space")
    res.append(_ok("colour space bt709", cs in (None, "bt709"), str(cs)))
    res.append(_ok("audio codec aac", a.get("codec_name") == "aac", str(a.get("codec_name"))))
    res.append(_ok("audio 48 kHz", str(a.get("sample_rate")) == str(C.AUDIO_RATE),
                   str(a.get("sample_rate"))))
    dur = float(info["format"]["duration"])
    lo, hi = duration_range(n_words)
    res.append(_ok("duration in range", lo <= dur <= hi, f"{dur:.2f}s (allowed {lo:.0f}-{hi:.0f}s)"))
    vd, ad = float(v.get("duration", dur)), float(a.get("duration", dur))
    res.append(_ok("audio/video length match <=50 ms", abs(vd - ad) <= 0.05,
                   f"video {vd:.3f}s audio {ad:.3f}s diff {abs(vd - ad) * 1000:.0f} ms"))
    res.append(_ok("length equals timeline", abs(vd - expected_total) <= 0.05,
                   f"{vd:.3f}s vs plan {expected_total:.3f}s"))
    ld = media.measure_loudness(path)
    res.append(_ok("loudness within +-1 LU of -14", abs(ld["lufs"] - C.TARGET_LUFS) <= 1.0,
                   f"{ld['lufs']:.1f} LUFS"))
    if ld["true_peak"] is not None:
        res.append(_ok("true peak <= -1 dBTP", ld["true_peak"] <= C.TRUE_PEAK + 1e-6,
                       f"{ld['true_peak']:.2f} dBTP"))
    # words
    norm = [normalize(w) for w in words]
    res.append(_ok("no duplicate words", len(set(norm)) == len(norm), ", ".join(words)))
    res.append(_ok("word count", len(words) == n_words, f"{len(words)} words"))
    # images: flagged if upscaled; hard fail only for extreme upscaling
    for w, inf in zip(words, image_infos):
        flagged = inf["upscaled"] or inf["upscaled_at_zoom"]
        extreme = inf["cover_scale"] > 2.0
        res.append(_ok(f"image resolution: {w}", not extreme,
                       f"{inf['width']}x{inf['height']}" + (" (FLAGGED: upscaled)" if flagged else ""),
                       hard=True))
    return res


def check_assets(paths: dict) -> list:
    res = []
    for name, p in paths.items():
        ok = Path(p).exists() and Path(p).stat().st_size > 1000
        res.append(_ok(f"asset present: {name}", ok, str(p)))
    return res


def check_frame_not_blank(jpg: str | Path) -> dict:
    arr = np.asarray(Image.open(jpg).convert("L"), dtype=np.float32)
    return _ok("cover frame not blank", float(arr.std()) > 12.0, f"std={arr.std():.1f}")


def assert_all(results: list) -> None:
    failed = [r for r in results if r["hard"] and not r["ok"]]
    if failed:
        msg = "; ".join(f"{r['name']} -> {r['detail']}" for r in failed)
        raise E.WCError(E.GATE_FAILED, msg, "quality")
