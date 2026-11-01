"""Royalty-free background music, synthesized with numpy (no samples, no internet, no licence).

A calm, warm loop: soft pad chords (Cmaj7 - Am7 - Fmaj7 - G6), a gentle bass note and a
quiet plucked arpeggio. 20 s long and seamless (the release tail is folded onto the start).

    python -m engine.musicgen            # writes assets/music/lingobits_loop.mp3
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from . import config as C

SR = C.AUDIO_RATE
BPM = 96
BEAT = 60.0 / BPM
CHORDS = [  # (bass midi, chord midi notes)
    (36, [60, 64, 67, 71]),     # Cmaj7
    (33, [57, 60, 64, 67]),     # Am7
    (41, [57, 60, 65, 69]),     # Fmaj7
    (43, [55, 59, 62, 64]),     # G6
]
BEATS_PER_CHORD = 8


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _pad_note(freq: float, dur: float, tail: float) -> np.ndarray:
    n = int((dur + tail) * SR)
    t = np.arange(n) / SR
    x = np.zeros(n)
    for det, amp in ((-0.0035, 0.5), (0.0, 0.7), (0.0035, 0.5)):      # slow chorus: 3 detuned voices
        f = freq * (1 + det)
        x += amp * (np.sin(2 * np.pi * f * t) + 0.18 * np.sin(2 * np.pi * 2 * f * t)
                    + 0.05 * np.sin(2 * np.pi * 3 * f * t))
    att, rel = 0.9, tail
    env = np.ones(n)
    a = int(att * SR)
    env[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    r = int(rel * SR)
    env[int(dur * SR):] = np.cos(np.linspace(0, np.pi / 2, n - int(dur * SR))) ** 2
    return x * env


def _pluck(freq: float, dur: float = 1.1) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = (np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t * 6)) * np.exp(-t * 4.2)
    a = int(0.006 * SR)
    x[:a] *= np.linspace(0, 1, a)
    return x


def _bass(freq: float, dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.12 * np.sin(2 * np.pi * 2 * freq * t)
    env = np.ones(n)
    a, r = int(0.08 * SR), int(0.9 * SR)
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.minimum(env[-r:], np.linspace(1, 0, r))
    return x * env


def _add(buf: np.ndarray, clip: np.ndarray, t: float, gain: float) -> None:
    a = int(t * SR)
    b = min(len(buf), a + len(clip))
    if a < len(buf):
        buf[a:b] += clip[: b - a] * gain


def generate_loop() -> np.ndarray:
    """Return a seamless mono float32 loop (peak about 0.5)."""
    loop_len = len(CHORDS) * BEATS_PER_CHORD * BEAT
    tail = 3.0
    buf = np.zeros(int((loop_len + tail) * SR) + SR)
    rng = np.random.default_rng(7)
    for ci, (bass, notes) in enumerate(CHORDS):
        t0 = ci * BEATS_PER_CHORD * BEAT
        dur = BEATS_PER_CHORD * BEAT
        for m in notes:
            _add(buf, _pad_note(_hz(m), dur + 0.4, tail), t0, 0.16)
        _add(buf, _bass(_hz(bass), dur), t0, 0.34)
        # quiet arpeggio on eighth notes, a different order each chord
        order = list(notes) + [notes[1] + 12, notes[2] + 12]
        seq = [order[(k * (2 + ci % 2)) % len(order)] for k in range(BEATS_PER_CHORD * 2)]
        for k, m in enumerate(seq):
            if k % 4 == 3 and ci % 2:      # leave a few gaps: it should breathe
                continue
            _add(buf, _pluck(_hz(m + 12)), t0 + k * BEAT / 2, 0.075 * rng.uniform(0.8, 1.1))
    n = int(loop_len * SR)
    out = buf[:n].copy()
    wrap = buf[n:n + int(tail * SR)]
    out[: len(wrap)] += wrap                 # fold the release tail onto the beginning -> seamless loop
    # soft echo for space (circular, so the loop stays seamless)
    for ms, g in ((330, 0.22), (660, 0.10)):
        out = out + np.roll(out, int(ms / 1000 * SR)) * g
    out = out - out.mean()
    out /= np.max(np.abs(out)) + 1e-9
    return (out * 0.5).astype(np.float32)


def write_mp3(x: np.ndarray, dest: Path) -> Path:
    from . import media
    import tempfile
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "m.wav"
        media.write_wav(wav, np.stack([x, x], axis=1))
        p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame",
                            "-b:a", "160k", str(dest)], capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="ignore")[:300])
    return dest


def main() -> int:
    dest = C.MUSIC_DIR / "lingobits_loop.mp3"
    write_mp3(generate_loop(), dest)
    print(f"wrote {dest} ({dest.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
