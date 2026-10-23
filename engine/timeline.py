"""Timeline maths. Pure functions, no rendering, fully unit-tested.

Every time is derived from real audio durations; nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import config as C


@dataclass
class WordAudio:
    """Real durations (seconds) of the clips for one word."""
    question: float
    answer: float
    slow: float
    letters: int


@dataclass
class WordSeg:
    index: int
    start: float          # absolute start of this word segment
    q_start: float        # relative to start
    q_end: float
    cd_start: float       # countdown "2" begins
    cd_end: float         # == reveal
    reveal: float
    a_start: float
    a_end: float
    slow_start: float
    slow_end: float
    spell_start: float
    spell_step: float
    letters: int
    content_end: float    # end of spelling + hold, before transition
    duration: float       # includes the transition

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Timeline:
    hook_start: float
    hook_dur: float
    words: list = field(default_factory=list)
    outro_start: float = 0.0
    outro_dur: float = 0.0
    total: float = 0.0

    def frames(self) -> int:
        return int(round(self.total * C.FPS))

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def hook_duration(voice_sec: float) -> float:
    return max(C.HOOK_SEC, voice_sec + 0.5)


def outro_duration(voice_sec: float) -> float:
    return max(C.OUTRO_SEC, voice_sec + 0.8)


def word_segment(index: int, start: float, a: WordAudio) -> WordSeg:
    q_start = C.SLIDE_IN_SEC
    q_end = q_start + a.question
    cd_start = q_end + 0.10
    cd_end = cd_start + C.COUNTDOWN_MIN          # 2 -> 1, one second each
    reveal = cd_end
    a_start = reveal + 0.12
    a_end = a_start + a.answer
    slow_start = a_end + 0.30
    slow_end = slow_start + a.slow
    spell_start = slow_start
    n = max(1, a.letters)
    # letters appear across the slow pronunciation, clamped to a pleasant rate
    step = min(0.30, max(0.10, a.slow / n))
    spell_end = spell_start + step * n
    content_end = max(slow_end, spell_end) + 0.55
    duration = max(C.MIN_WORD_SEC, content_end) + C.TRANSITION_SEC
    return WordSeg(index, start, q_start, q_end, cd_start, cd_end, reveal,
                   a_start, a_end, slow_start, slow_end, spell_start, step,
                   a.letters, content_end, duration)


def build_timeline(hook_voice: float, words: list, outro_voice: float) -> Timeline:
    hook = hook_duration(hook_voice)
    t = hook
    segs = []
    for i, wa in enumerate(words):
        seg = word_segment(i, t, wa)
        segs.append(seg)
        t = seg.end
    outro = outro_duration(outro_voice)
    total = t + outro
    # snap to whole frames so audio and video lengths match exactly
    frames = int(-(-total * C.FPS // 1))
    total = frames / C.FPS
    return Timeline(0.0, hook, segs, t, total - t, total)


def audio_events(tl: Timeline) -> list:
    """(absolute_time, kind, key) for everything that must be heard.

    kind 'voice' keys: hook, q<i>, a<i>, s<i>, outro
    kind 'sfx' keys: whoosh, tick, ding, pop, key, sparkle
    """
    ev = [(0.15, "voice", "hook"), (0.05, "sfx", "pop")]
    for i in range(3):
        ev.append((0.25 + i * 0.28, "sfx", "pop"))
    for seg in tl.words:
        s = seg.start
        ev.append((s + 0.02, "sfx", "whoosh"))
        ev.append((s + seg.q_start, "voice", f"q{seg.index}"))
        ev.append((s + seg.cd_start, "sfx", "tick"))
        ev.append((s + seg.cd_start + 1.0, "sfx", "tick"))
        ev.append((s + seg.reveal, "sfx", "ding"))
        ev.append((s + seg.reveal + 0.02, "sfx", "pop"))
        ev.append((s + seg.reveal + 0.05, "sfx", "sparkle"))
        ev.append((s + seg.a_start, "voice", f"a{seg.index}"))
        ev.append((s + seg.slow_start, "voice", f"s{seg.index}"))
        for k in range(seg.letters):
            ev.append((s + seg.spell_start + k * seg.spell_step, "sfx", "key"))
        ev.append((s + seg.duration - C.TRANSITION_SEC, "sfx", "whoosh"))
    ev.append((tl.outro_start + 0.1, "voice", "outro"))
    ev.append((tl.outro_start + 0.05, "sfx", "pop"))
    ev.sort(key=lambda e: e[0])
    return ev


def ease_out_back(t: float, s: float = 1.70158) -> float:
    t = min(1.0, max(0.0, t)) - 1.0
    return 1.0 + (s + 1.0) * t ** 3 + s * t ** 2


def ease_in_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t ** 3


def ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def spring_scale(t: float, dur: float = 0.40) -> float:
    """0.6 -> 1.08 -> 1.0 pop used by the answer label."""
    if t <= 0:
        return 0.6
    if t >= dur:
        return 1.0
    u = t / dur
    if u < 0.6:
        return 0.6 + (1.08 - 0.6) * ease_out_cubic(u / 0.6)
    return 1.08 + (1.0 - 1.08) * ease_out_cubic((u - 0.6) / 0.4)


def zoom_at(t_rel: float, seg_dur: float) -> float:
    """Ken Burns 1.00 -> 1.12 across the whole word."""
    u = min(1.0, max(0.0, t_rel / max(seg_dur, 1e-6)))
    return 1.0 + (C.KEN_BURNS_END - 1.0) * u
