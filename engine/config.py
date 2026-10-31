"""WordClip Studio - shared constants and config loading.

All layout numbers live here so the renderer, the unit tests and the
frontend preview agree on the exact geometry of the final video.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is installed in the workflow
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONT_DIR = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
SOCIAL_DIR = ASSETS / "social"     # youtube.png instagram.png tiktok.png facebook.png
SOCIAL_ORDER = ("youtube", "instagram", "tiktok", "facebook")

# ---------------------------------------------------------------- video spec
W, H = 1080, 1920
FPS = 30
SUPERSAMPLE = 2          # card/text are drawn at 2x then downsampled
CRF = 17
PRESET = "slow"
AUDIO_RATE = 48000
AUDIO_BITRATE = "192k"
TARGET_LUFS = -14.0
TRUE_PEAK = -1.0

# ------------------------------------------------------------- safe zones
SAFE_X0, SAFE_X1 = 60, 960      # important content must stay inside
SAFE_Y0, SAFE_Y1 = 160, 1560

# ------------------------------------------------------------------ layout
TITLE_BOX = (SAFE_X0, 170, SAFE_X1, 300)            # x0, y0, x1, y1
CARD_X, CARD_Y, CARD_W, CARD_H = 90, 330, 900, 1000
CARD_RADIUS = 40
ANSWER_BOX = (SAFE_X0, 1370, SAFE_X1, 1560)
RING_CENTER = (540, 1462)
RING_RADIUS = 84
RING_OUTLINE = 8
# progress pills sit on the first line of the title band, inside the safe zone
PILL_BOX = (SAFE_X0, 170, SAFE_X1, 204)
TITLE_TEXT_BOX = (SAFE_X0, 210, SAFE_X1, 300)
# centred content is centred on x=540 while the safe zone is 60..960, so
# centred elements may be at most this wide (including pop overshoot)
CENTER_X = 540
SAFE_HALF_W = SAFE_X1 - CENTER_X          # 420
LABEL_BOX = (SAFE_X0, 1370, SAFE_X1, 1475)
SPELL_Y = (1492, 1552)
LABEL_POP_MAX = 1.08

# --------------------------------------------------------------- timeline
HOOK_SEC = 2.0
OUTRO_SEC = 3.0
SLIDE_IN_SEC = 0.45
ARROW_DELAY = 0.20
COUNTDOWN_MIN = 2.0
TRANSITION_SEC = 0.30
KEN_BURNS_END = 1.12
SLOW_ATEMPO = 0.8
SPELL_STEP_SEC = 0.16
MIN_WORD_SEC = 6.0
DURATION_RANGE = (20.0, 40.0)

# ------------------------------------------------------------------ themes
# bg1/bg2: background gradient, accent: brush corners/title, ring colours,
# label_bg/label_fg: answer label (contrast >= 4.5:1, verified in tests)
THEMES = [
    dict(name="yellow", bg1="#FFFFFF", bg2="#EDEDED", accent="#F2B705",
         accent2="#C9500F", title_fill="#F7D060", title_edge="#B5400A",
         ring_a="#FF4FA3", ring_b="#2EE6D6", label_bg="#FFE600", label_fg="#111111",
         ink="#161616"),
    dict(name="blue", bg1="#F5FAFF", bg2="#DCEBFA", accent="#1E6FE0",
         accent2="#0B3C8C", title_fill="#8EC5FF", title_edge="#0B3C8C",
         ring_a="#FFB020", ring_b="#2EE6D6", label_bg="#0B3C8C", label_fg="#FFFFFF",
         ink="#0B1B33"),
    dict(name="green", bg1="#F6FFF6", bg2="#DDF3DD", accent="#2DA84F",
         accent2="#0F5F2A", title_fill="#9BE7A7", title_edge="#0F5F2A",
         ring_a="#FFD23F", ring_b="#2EC4B6", label_bg="#0F5F2A", label_fg="#FFFFFF",
         ink="#0E2A16"),
    dict(name="purple", bg1="#FBF7FF", bg2="#E8DCF7", accent="#7B3FE4",
         accent2="#43198F", title_fill="#C9A8FF", title_edge="#43198F",
         ring_a="#FF6FB5", ring_b="#35E0E0", label_bg="#43198F", label_fg="#FFFFFF",
         ink="#220B45"),
    dict(name="orange", bg1="#FFFaf3", bg2="#FFE6CC", accent="#FF7A1A",
         accent2="#A33B00", title_fill="#FFC48A", title_edge="#A33B00",
         ring_a="#7B3FE4", ring_b="#FFE600", label_bg="#A33B00", label_fg="#FFFFFF",
         ink="#3A1700"),
    dict(name="teal", bg1="#F2FFFE", bg2="#D3F3F0", accent="#12A8A0",
         accent2="#096661", title_fill="#8FEDE6", title_edge="#096661",
         ring_a="#FF5C8A", ring_b="#FFD23F", label_bg="#096661", label_fg="#FFFFFF",
         ink="#06312F"),
]


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(hex_to_rgb(a)), luminance(hex_to_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def pick_theme(seed: str, override: str | None = None) -> dict:
    """Rotate themes automatically; the seed (session id) keeps it stable."""
    if override:
        for t in THEMES:
            if t["name"] == override:
                return t
    idx = sum(ord(c) for c in seed) % len(THEMES)
    return THEMES[idx]


# ------------------------------------------------------------ user config
@dataclass
class Settings:
    channel_handle: str = ""          # optional text under the social icons
    channel_spoken_name: str = ""     # how the voice says the channel name (empty = derived from handle)
    outro_follow: bool = True         # outro voice also says "follow <channel>"
    words_per_video: int = 3
    theme: str = "auto"
    use_vocal_tags: bool = False
    music_volume_lufs: float = -26.0
    duck_db: float = -10.0
    tts_model: str = "canopylabs/orpheus-v1-english"
    voice_question: str = "autumn"
    voice_answer: str = "troy"
    text_model: str = "llama-3.3-70b-versatile"
    vision_model: str = "qwen/qwen3.6-27b"
    vision_check: bool = True         # drop photos that do not show the word
    vision_gate: bool = True          # build fails if a chosen photo clearly is not the word
    edge_voice_question: str = "en-US-AvaNeural"
    edge_voice_answer: str = "en-US-AndrewNeural"
    categories: list = field(default_factory=lambda: [
        "kitchen", "tools", "garden", "office", "bathroom", "clothing",
        "furniture", "toys", "sports", "vehicles", "food", "electronics"])


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """config.yaml first, then GitHub Actions Variables (env) override."""
    s = Settings()
    p = Path(path) if path else ROOT / "config.yaml"
    if yaml is not None and p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if hasattr(s, k) and v is not None:
                setattr(s, k, v)
    env_map = {
        "GROQ_TEXT_MODEL": "text_model",
        "GROQ_VISION_MODEL": "vision_model",
        "GROQ_TTS_MODEL": "tts_model",
        "GROQ_TTS_VOICE_QUESTION": "voice_question",
        "GROQ_TTS_VOICE_ANSWER": "voice_answer",
    }
    for env, attr in env_map.items():
        v = os.environ.get(env, "").strip()
        if v:
            setattr(s, attr, v)
    s.words_per_video = int(s.words_per_video)
    return s


def spoken_channel_name(settings) -> str:
    """'@Lingobits-Official' -> 'Lingobits'. config.yaml `channel_spoken_name` wins."""
    import re
    explicit = (getattr(settings, "channel_spoken_name", "") or "").strip()
    if explicit:
        return explicit
    h = (getattr(settings, "channel_handle", "") or "").strip().lstrip("@")
    parts = [p for p in re.split(r"[-_.\s]+", h) if p and p.lower() not in ("official", "channel", "tv", "yt")]
    name = " ".join(parts) if parts else h
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)      # CamelCase -> Camel Case
    return re.sub(r"[^A-Za-z0-9 ]", "", name).strip()
