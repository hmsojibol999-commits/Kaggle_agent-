"""Mix voices + SFX (+ optional music bed) into one stereo track at -14 LUFS."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from . import config as C
from . import media
from .timeline import Timeline, audio_events

SR = C.AUDIO_RATE
SFX_GAIN = {"whoosh": 0.45, "tick": 0.45, "ding": 0.50, "pop": 0.45,
            "key": 0.55, "sparkle": 0.35}
VOICE_GAIN = 1.0


def place(buf: np.ndarray, clip: np.ndarray, t: float, gain: float) -> None:
    a = int(round(t * SR))
    if a >= len(buf) or a < 0:
        return
    b = min(len(buf), a + len(clip))
    buf[a:b] += clip[: b - a] * gain


def voice_activity_envelope(voice_track: np.ndarray, rate_hz: int = 200) -> np.ndarray:
    """Per-control-step voice presence (0..1)."""
    hop = SR // rate_hz
    n = len(voice_track) // hop
    if n == 0:
        return np.zeros(1)
    seg = np.abs(voice_track[: n * hop]).reshape(n, hop).max(axis=1)
    return (seg > 0.02).astype(np.float64)


def duck_curve(activity: np.ndarray, rate_hz: int, duck_db: float,
               attack: float = 0.08, release: float = 0.35) -> np.ndarray:
    """Smooth gain curve: 1.0 when silent, 10**(duck_db/20) under voice."""
    floor = 10 ** (duck_db / 20.0)
    g = np.empty_like(activity)
    cur = 1.0
    a_k = 1.0 - np.exp(-1.0 / (attack * rate_hz))
    r_k = 1.0 - np.exp(-1.0 / (release * rate_hz))
    for i, act in enumerate(activity):
        target = floor if act > 0.5 else 1.0
        k = a_k if target < cur else r_k
        cur += k * (target - cur)
        g[i] = cur
    return g


def load_music(music_dir: Path, total_sec: float, target_lufs: float) -> np.ndarray | None:
    files = sorted(Path(music_dir).glob("*.mp3")) if Path(music_dir).exists() else []
    if not files:
        return None
    x = media.decode_audio(files[0])
    if len(x) < SR:
        return None
    n = int(total_sec * SR)
    reps = int(np.ceil(n / len(x)))
    x = np.tile(x, reps)[:n].copy()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.wav"
        media.write_wav(p, np.clip(x, -1, 1))
        try:
            lufs = media.measure_loudness(p)["lufs"]
        except Exception:
            lufs = -20.0
    x *= 10 ** ((target_lufs - lufs) / 20.0)
    return x.astype(np.float32)


def build_mix(tl: Timeline, clips: dict, sfx: dict, settings,
              music_dir: Path | None = None) -> tuple[np.ndarray, dict]:
    """Return (stereo [n,2] float32 normalised to target LUFS, info dict)."""
    n = int(round(tl.total * SR))
    voice = np.zeros(n, dtype=np.float32)
    fx = np.zeros(n, dtype=np.float32)
    missing = []
    for t, kind, key in audio_events(tl):
        if kind == "voice":
            clip = clips.get(key)
            if clip is None:
                missing.append(key)
                continue
            place(voice, clip, t, VOICE_GAIN)
        else:
            clip = sfx.get(key)
            if clip is None:
                missing.append(key)
                continue
            place(fx, clip, t, SFX_GAIN[key])
    if missing:
        raise RuntimeError(f"missing audio assets: {sorted(set(missing))}")

    mix = voice + fx
    info = {"music": False}
    music = load_music(music_dir, tl.total, settings.music_volume_lufs) if music_dir else None
    if music is not None:
        hz = 200
        act = voice_activity_envelope(voice, hz)
        curve = duck_curve(act, hz, settings.duck_db)
        xs = np.arange(len(curve)) / hz
        gain = np.interp(np.arange(n) / SR, xs, curve).astype(np.float32)
        mix = mix + music[:n] * gain
        info["music"] = True

    # master fades
    fi, fo = int(0.10 * SR), int(0.40 * SR)
    mix[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    mix[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)

    # headroom before loudnorm, then two-pass normalisation
    pk = float(np.max(np.abs(mix))) + 1e-9
    mix = mix * (0.85 / pk) if pk > 0.85 else mix
    stereo = np.stack([mix, mix], axis=1)
    norm = media.loudnorm_two_pass(stereo, C.TARGET_LUFS, tp=-1.5)
    # make length exactly n (loudnorm may pad/trim by a few samples)
    if len(norm) < n:
        norm = np.pad(norm, ((0, n - len(norm)), (0, 0)))
    norm = norm[:n]
    info["peak"] = float(np.max(np.abs(norm)))
    return norm.astype(np.float32), info
