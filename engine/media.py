"""Thin ffmpeg/ffprobe helpers (audio decode, tempo change, probing, loudness)."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from . import config as C


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def decode_audio(path: str | Path) -> np.ndarray:
    """Any audio file -> mono float32 at C.AUDIO_RATE (via ffmpeg)."""
    p = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
             "-ar", str(C.AUDIO_RATE), "-"])
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(f"ffmpeg decode failed: {p.stderr.decode(errors='ignore')[:300]}")
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def write_wav(path: str | Path, mono_or_stereo: np.ndarray, rate: int = C.AUDIO_RATE) -> None:
    x = np.clip(mono_or_stereo, -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    ch = 1 if pcm.ndim == 1 else pcm.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def atempo(x: np.ndarray, tempo: float) -> np.ndarray:
    """Time-stretch keeping pitch (ffmpeg atempo)."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.wav"
        write_wav(src, x)
        p = run(["ffmpeg", "-v", "error", "-i", str(src), "-filter:a", f"atempo={tempo}",
                 "-f", "f32le", "-ac", "1", "-ar", str(C.AUDIO_RATE), "-"])
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="ignore")[:300])
        return np.frombuffer(p.stdout, dtype=np.float32).copy()


def probe(path: str | Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", str(path)])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="ignore")[:300])
    return json.loads(p.stdout)


def measure_loudness(path: str | Path) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio."""
    p = run(["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af",
             "ebur128=peak=true", "-f", "null", "-"])
    err = p.stderr.decode(errors="ignore")
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else err
    m_i = re.search(r"I:\s+(-?\d+\.?\d*)\s+LUFS", summary)
    m_p = re.search(r"Peak:\s+(-?\d+\.?\d*)\s+dBFS", summary)
    if not m_i:
        raise RuntimeError("could not parse loudness: " + err[-300:])
    return {"lufs": float(m_i.group(1)),
            "true_peak": float(m_p.group(1)) if m_p else None}


def loudnorm_two_pass(x: np.ndarray, target: float = C.TARGET_LUFS,
                      tp: float = -1.5) -> np.ndarray:
    """Two-pass loudnorm on an in-memory stereo array [n,2] -> same shape."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "mix.wav"
        write_wav(src, x)
        flt = f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json"
        p1 = run(["ffmpeg", "-nostats", "-i", str(src), "-af", flt, "-f", "null", "-"])
        txt = p1.stderr.decode(errors="ignore")
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, flags=re.S)
        if not m:
            raise RuntimeError("loudnorm pass 1 produced no stats: " + txt[-300:])
        s = json.loads(m.group(0))
        flt2 = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={s['input_i']}:"
                f"measured_LRA={s['input_lra']}:measured_TP={s['input_tp']}:"
                f"measured_thresh={s['input_thresh']}:offset={s['target_offset']}:"
                f"linear=true:print_format=summary")
        out = Path(d) / "norm.wav"
        p2 = run(["ffmpeg", "-v", "error", "-i", str(src), "-af", flt2,
                  "-ar", str(C.AUDIO_RATE), "-c:a", "pcm_s16le", str(out)])
        if p2.returncode != 0 or not out.exists():
            raise RuntimeError("loudnorm pass 2 failed: " + p2.stderr.decode(errors="ignore")[-300:])
        with wave.open(str(out), "rb") as w:
            raw = w.readframes(w.getnframes())
            ch = w.getnchannels()
        y = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return y.reshape(-1, ch)
