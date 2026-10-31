"""Voice rendering: Groq Orpheus -> edge-tts -> (mock tones in --mock).

Every clip is cached by sha1(engine, model, voice, text), silence-trimmed,
peak/RMS-normalised and given 30 ms fades. Which engine produced each clip is
recorded in `engines_used` for the release notes.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import numpy as np
import requests

from . import config as C
from . import errors as E
from . import llm
from . import media

GROQ_SPEECH = "https://api.groq.com/openai/v1/audio/speech"
FEMALE_VOICES = ["autumn", "diana", "hannah"]
MALE_VOICES = ["austin", "daniel", "troy"]

FADE_SEC = 0.030


def calm_text(text: str) -> str:
    """Orpheus acts out '?' and '!' (rising, excited voice). A flat full stop gives
    a natural, steady narrator, so every spoken line ends with '.' instead."""
    t = " ".join((text or "").replace("\u2019", "'").split())
    t = t.replace("?", ".").replace("!", ".").replace("...", ".")
    while ".." in t:
        t = t.replace("..", ".")
    t = t.replace(" .", ".").replace(".,", ",")
    if t and t[-1] not in ".":
        t += "."
    return t


def _log(msg: str) -> None:
    print(f"[tts] {msg}", flush=True)


# ------------------------------------------------------------ clip cleanup
def trim_silence(x: np.ndarray, thresh_db: float = -45.0, pad: float = 0.04) -> np.ndarray:
    if len(x) == 0:
        return x
    peak = float(np.max(np.abs(x))) + 1e-9
    thr = peak * (10 ** (thresh_db / 20.0))
    idx = np.where(np.abs(x) > thr)[0]
    if len(idx) == 0:
        return x
    a = max(0, idx[0] - int(pad * C.AUDIO_RATE))
    b = min(len(x), idx[-1] + int(pad * C.AUDIO_RATE))
    return x[a:b]


def normalize_clip(x: np.ndarray, target_rms_db: float = -20.0, peak_db: float = -2.0) -> np.ndarray:
    if len(x) == 0:
        return x
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-9
    gain = (10 ** (target_rms_db / 20.0)) / rms
    y = x * gain
    pk = float(np.max(np.abs(y))) + 1e-9
    limit = 10 ** (peak_db / 20.0)
    if pk > limit:
        y *= limit / pk
    return y.astype(np.float32)


def fade(x: np.ndarray, sec: float = FADE_SEC) -> np.ndarray:
    n = min(int(sec * C.AUDIO_RATE), len(x) // 2)
    if n <= 0:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def clean_clip(x: np.ndarray) -> np.ndarray:
    return fade(normalize_clip(trim_silence(x)))


# --------------------------------------------------------------- engines
def _groq_render(text: str, model: str, voice: str, key: str, dest: Path) -> None:
    last: E.WCError | None = None
    for attempt in range(3):
        try:
            r = requests.post(
                GROQ_SPEECH,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "input": text, "voice": voice, "response_format": "wav"},
                timeout=90,
            )
        except requests.RequestException as ex:
            last = E.WCError(E.TTS_FAILED, str(ex), "tts")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200 and r.content[:4] == b"RIFF":
            dest.write_bytes(r.content)
            return
        body = r.text[:300]
        low = body.lower()
        if "terms" in low and ("accept" in low or "required" in low):
            raise E.WCError(E.GROQ_TERMS, body, "tts")
        if r.status_code in (401,):
            raise E.WCError(E.GROQ_NO_KEY, body, "tts")
        if "restricted" in low:                      # organisation blocked by Groq: retrying is pointless
            raise E.WCError(E.GROQ_MODEL, f"HTTP {r.status_code}: {body}", "tts")
        if r.status_code in (403, 404):
            raise E.WCError(E.GROQ_MODEL, f"HTTP {r.status_code}: {body}", "tts")
        if r.status_code == 429:
            last = E.WCError(E.GROQ_RATE, body, "tts")
            time.sleep(2 ** (attempt + 1))
            continue
        last = E.WCError(E.TTS_FAILED, f"HTTP {r.status_code}: {body}", "tts")
        time.sleep(2 ** attempt)
    raise last or E.WCError(E.TTS_FAILED, "unknown", "tts")


def _edge_render(text: str, voice: str, dest: Path, rate: str = "+0%") -> None:
    import edge_tts  # type: ignore

    async def go():
        comm = edge_tts.Communicate(text, voice, rate=rate)
        await comm.save(str(dest))

    last = None
    for attempt in range(3):
        try:
            asyncio.run(go())
            if dest.exists() and dest.stat().st_size > 500:
                return
        except Exception as ex:   # edge-tts is unofficial; network errors are common
            last = ex
            time.sleep(2 ** attempt)
    raise E.WCError(E.TTS_FAILED, f"edge-tts failed: {last}", "tts")


def mock_tone(text: str, role: str, slow: bool = False) -> np.ndarray:
    """Speech-like synthetic tones for --mock: one blip per syllable-ish chunk."""
    sr = C.AUDIO_RATE
    base = 210.0 if role == "question" else 125.0
    per = 0.085 * (1.25 if slow else 1.0)
    chars = max(3, len(text))
    n = int(chars * per * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    rng = np.random.default_rng(len(text) * 7 + (1 if role == "question" else 2))
    seg = int(0.17 * sr)
    for s in range(0, n, seg):
        f = base * rng.uniform(0.85, 1.35)
        e = min(n, s + int(seg * 0.8))
        tt = t[s:e] - t[s]
        env = np.sin(np.pi * np.linspace(0, 1, e - s)) ** 1.5
        x[s:e] += (np.sin(2 * np.pi * f * tt) + 0.4 * np.sin(2 * np.pi * 2 * f * tt)) * env
    return (x / (np.max(np.abs(x)) + 1e-9) * 0.5).astype(np.float32)


class VoiceEngine:
    def __init__(self, settings, cache_dir: str | Path, mock: bool = False,
                 groq_key: str | None = None):
        self.s = settings
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.mock = mock
        self.key = groq_key if groq_key is not None else llm.groq_key()
        self.groq_dead = not self.key and not mock   # circuit breaker
        self.groq_reason = "" if self.key or mock else "GROQ_API_KEY missing"
        self.engines_used: dict = {}                 # engine -> clip count
        self.used_files: list = []                   # (text, cached path)
        self.fallback_notes: list = []

    # -- helpers
    def _key(self, engine: str, voice: str, text: str, extra: str = "") -> str:
        h = hashlib.sha1(f"{engine}|{self.s.tts_model}|{voice}|{text}|{extra}".encode()).hexdigest()
        return h[:20]

    def _count(self, engine: str) -> None:
        self.engines_used[engine] = self.engines_used.get(engine, 0) + 1

    def _vocal(self, text: str, role: str) -> str:
        text = calm_text(text)
        if self.s.use_vocal_tags:
            return f"[cheerful] {text}"
        return text

    # -- public
    def render(self, text: str, role: str, slow: bool = False) -> np.ndarray:
        """Return a cleaned mono clip. role: 'question' | 'answer'."""
        text = text.strip()
        if self.mock:
            self._count("mock-tone")
            x = mock_tone(text, role, slow)
            return clean_clip(x)
        voice_g = self.s.voice_question if role == "question" else self.s.voice_answer
        voice_e = self.s.edge_voice_question if role == "question" else self.s.edge_voice_answer
        engines = []
        if not self.groq_dead:
            engines.append(("groq", voice_g))
        engines.append(("edge", voice_e))
        last_err: Exception | None = None
        for engine, voice in engines:
            try:
                raw = self._render_engine(engine, voice, text, role)
            except E.WCError as ex:
                last_err = ex
                if engine == "groq":
                    self.groq_dead = ex.code in (E.GROQ_MODEL, E.GROQ_TERMS, E.GROQ_NO_KEY) or self.groq_dead
                    self.groq_reason = f"{ex.code}: {ex.detail[:160]}"
                    note = f"Groq TTS failed ({ex.code}); using edge-tts fallback"
                    if note not in self.fallback_notes:
                        self.fallback_notes.append(note)
                    _log(f"WARNING {note}: {ex.detail[:200]}")
                continue
            except Exception as ex:  # noqa: BLE001
                last_err = ex
                _log(f"WARNING {engine} failed: {ex}")
                continue
            self._count(engine)
            if slow:
                raw = media.atempo(raw, C.SLOW_ATEMPO)
            return clean_clip(raw)
        raise E.WCError(E.TTS_FAILED, f"all voice engines failed: {last_err}", "tts")

    def _render_engine(self, engine: str, voice: str, text: str, role: str) -> np.ndarray:
        spoken = self._vocal(text, role) if engine == "groq" else text
        key = self._key(engine, voice, spoken)
        ext = "wav" if engine == "groq" else "mp3"
        path = self.cache / f"{key}.{ext}"
        if not path.exists() or path.stat().st_size < 500:
            tmp = path.with_suffix(path.suffix + ".part")
            if engine == "groq":
                _groq_render(spoken, self.s.tts_model, voice, self.key, tmp)
            else:
                _edge_render(spoken, voice, tmp)
            tmp.replace(path)
        self.used_files.append((text, str(path)))
        return media.decode_audio(path)

    def engines_summary(self) -> str:
        if not self.engines_used:
            return "none"
        return ", ".join(f"{k} x{v}" for k, v in sorted(self.engines_used.items()))
