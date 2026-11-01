"""Sound effects synthesized with numpy: no samples, no licences.

All functions return mono float32 arrays at C.AUDIO_RATE in [-1, 1].
A fixed RNG seed keeps the output identical between runs.
"""
from __future__ import annotations

import numpy as np

from . import config as C

SR = C.AUDIO_RATE


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR), dtype=np.float64) / SR


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.minimum(e[-r:], np.linspace(1, 0, r))
    return e


def _lowpass_noise(n: int, rng, cutoff_curve: np.ndarray) -> np.ndarray:
    """One-pole low-pass with a time-varying cutoff (cheap filtered-noise sweep)."""
    noise = rng.standard_normal(n)
    out = np.empty(n)
    y = 0.0
    a = 1.0 - np.exp(-2.0 * np.pi * cutoff_curve / SR)
    for i in range(n):
        y += a[i] * (noise[i] - y)
        out[i] = y
    return out


def whoosh(dur: float = 0.38) -> np.ndarray:
    rng = np.random.default_rng(11)
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    # cutoff sweeps up then down: a classic whoosh shape
    cutoff = 250 + 3600 * np.sin(np.pi * t) ** 1.6   # darker, less hissy
    x = _lowpass_noise(n, rng, cutoff)
    x /= np.max(np.abs(x)) + 1e-9
    env = np.sin(np.pi * t) ** 2
    return (x * env * 0.50).astype(np.float32)


def tick(dur: float = 0.12) -> np.ndarray:
    t = _t(dur)
    x = np.sin(2 * np.pi * 1250 * t) * np.exp(-t * 48)
    x += 0.35 * np.sin(2 * np.pi * 2500 * t) * np.exp(-t * 70)
    x *= _env(len(t), 0.002, 0.03)                  # no click at the start
    return (x * 0.5).astype(np.float32)


def ding(dur: float = 1.1) -> np.ndarray:
    """Bell-like: inharmonic sine partials with different decays."""
    t = _t(dur)
    base = 1046.5  # C6
    partials = [(1.0, 1.0, 3.2), (2.76, 0.55, 4.5), (5.4, 0.30, 7.0), (8.93, 0.14, 10.0)]
    x = np.zeros_like(t)
    for ratio, amp, decay in partials:
        x += amp * np.sin(2 * np.pi * base * ratio * t) * np.exp(-t * decay)
    x *= _env(len(t), 0.002, 0.05)
    d = int(0.11 * SR)                              # soft echo = a little room
    echo = np.zeros_like(x)
    echo[d:] = x[:-d] * 0.28
    x = x + echo
    return (x / np.max(np.abs(x)) * 0.52).astype(np.float32)


def pop(dur: float = 0.14) -> np.ndarray:
    t = _t(dur)
    f = 460 * np.exp(-t * 20) + 170            # quick downward pitch drop
    phase = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(phase) * np.exp(-t * 26)
    x *= _env(len(t), 0.003, 0.04)
    return (x * 0.55).astype(np.float32)


def key_click(dur: float = 0.06) -> np.ndarray:
    rng = np.random.default_rng(5)
    t = _t(dur)
    noise = rng.standard_normal(len(t)) * np.exp(-t * 140)
    thump = np.sin(2 * np.pi * 240 * t) * np.exp(-t * 90)
    x = 0.55 * noise + 0.7 * thump
    return (x / (np.max(np.abs(x)) + 1e-9) * 0.33).astype(np.float32)


def sparkle(dur: float = 0.8) -> np.ndarray:
    rng = np.random.default_rng(23)
    out = np.zeros(int(dur * SR))
    for i in range(7):
        start = int(rng.uniform(0, 0.45) * SR)
        f = rng.choice([2093.0, 2637.0, 3136.0, 3520.0, 4186.0])
        d = 0.18
        t = _t(d)
        s = np.sin(2 * np.pi * f * t) * np.exp(-t * 18)
        end = min(len(out), start + len(s))
        out[start:end] += s[: end - start] * rng.uniform(0.3, 0.6)
    out *= _env(len(out), 0.002, 0.1)
    return (out / (np.max(np.abs(out)) + 1e-9) * 0.28).astype(np.float32)


_BANK = {
    "whoosh": whoosh,
    "tick": tick,
    "ding": ding,
    "pop": pop,
    "key": key_click,
    "sparkle": sparkle,
}


def make_all() -> dict:
    return {k: fn() for k, fn in _BANK.items()}
