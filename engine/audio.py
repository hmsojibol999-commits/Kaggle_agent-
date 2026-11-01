"""Mix voices + SFX + music bed into one stereo track at -14 LUFS (never clips)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from . import config as C
from . import media
from .timeline import Timeline, audio_events

SR = C.AUDIO_RATE
# effects sit clearly UNDER the voice so the narrator stays easy to understand
SFX_GAIN = {"whoosh": 0.36, "tick": 0.40, "ding": 0.42, "chime": 0.46, "pop": 0.34,
            "key": 0.40, "sparkle": 0.22}
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


def highpass(x: np.ndarray, cutoff: float = 85.0) -> np.ndarray:
    """One-pole high-pass (removes rumble/pops below ~85 Hz)."""
    a = float(np.exp(-2 * np.pi * cutoff / SR))
    y = np.empty_like(x)
    px = py = 0.0
    for i in range(len(x)):
        v = float(x[i])
        py = a * (py + v - px)
        px = v
        y[i] = py
    return y


def compress(x: np.ndarray, thresh_db: float = -22.0, ratio: float = 2.2,
             hz: int = 200) -> np.ndarray:
    """Light feed-forward compressor on the voice (evens out loud and quiet words)."""
    hop = SR // hz
    n = len(x) // hop
    if n == 0:
        return x
    env = np.abs(x[: n * hop]).reshape(n, hop).max(axis=1) + 1e-6
    lvl = 20 * np.log10(env)
    over = np.maximum(0.0, lvl - thresh_db)
    gain_db = -over * (1 - 1 / ratio)
    g = np.empty(n)
    cur = 0.0
    a_k, r_k = 1 - np.exp(-1 / (0.01 * hz)), 1 - np.exp(-1 / (0.12 * hz))
    for i in range(n):                      # attack 10 ms, release 120 ms
        k = a_k if gain_db[i] < cur else r_k
        cur += k * (gain_db[i] - cur)
        g[i] = cur
    gl = np.interp(np.arange(len(x)) / SR, np.arange(n) / hz, 10 ** (g / 20)).astype(np.float32)
    return (x * gl * 10 ** (2.0 / 20)).astype(np.float32)     # +2 dB make-up


def reverb_tail(x: np.ndarray, wet: float = 0.07, decay: float = 0.28, seed: int = 3) -> np.ndarray:
    """Very light room: convolve with a short decaying noise burst (FFT), keep only the wet part."""
    n_ir = int(decay * 1.6 * SR)
    t = np.arange(n_ir) / SR
    ir = np.random.default_rng(seed).standard_normal(n_ir) * np.exp(-t * (6.9 / decay))
    ir[: int(0.012 * SR)] = 0.0             # 12 ms pre-delay keeps speech clear
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(x) + n_ir)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return (y * wet).astype(np.float32)


def polish_voice(voice: np.ndarray) -> np.ndarray:
    """High-pass + light compression. Reverb is added separately so ducking ignores it."""
    return compress(highpass(voice)).astype(np.float32)


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
    return fade_music(x.astype(np.float32), fade_in=0.8, fade_out=1.8)


def fade_music(x: np.ndarray, fade_in: float = 0.8, fade_out: float = 1.8) -> np.ndarray:
    """Smooth fade in at the start and fade out at the end of the music bed."""
    y = x.copy()
    a, b = min(len(y), int(fade_in * SR)), min(len(y), int(fade_out * SR))
    if a > 0:
        y[:a] *= np.sin(np.linspace(0, np.pi / 2, a, dtype=np.float32)) ** 2
    if b > 0:
        y[-b:] *= np.cos(np.linspace(0, np.pi / 2, b, dtype=np.float32)) ** 2
    return y


def soft_limit(x: np.ndarray, ceiling: float = 0.93) -> np.ndarray:
    """Transparent tanh-style soft clip: values below ~0.7*ceiling pass almost unchanged,
    nothing ever exceeds `ceiling`."""
    knee = 0.7 * ceiling
    y = x.copy()
    over = np.abs(y) > knee
    if over.any():
        sign = np.sign(y[over])
        excess = (np.abs(y[over]) - knee) / (ceiling - knee)
        y[over] = sign * (knee + (ceiling - knee) * np.tanh(excess))
    return y


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

    voice = polish_voice(voice)
    mix = voice + reverb_tail(voice) + fx
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
    mix = soft_limit(mix, 0.95)
    stereo = np.stack([mix, mix], axis=1)
    norm = media.loudnorm_two_pass(stereo, C.TARGET_LUFS, tp=-1.5)
    # make length exactly n (loudnorm may pad/trim by a few samples)
    if len(norm) < n:
        norm = np.pad(norm, ((0, n - len(norm)), (0, 0)))
    norm = soft_limit(norm[:n], 0.89)           # 0.89 = -1.0 dBFS: hard guarantee against clipping
    info["peak"] = float(np.max(np.abs(norm)))
    return norm.astype(np.float32), info
