"""Parse and validate the compact selection JSON sent by the UI to build.yml.

Format (kept small, well under 60k chars):
{"v":1,"sid":"20261021-0930-ab12","theme":"auto",
 "w":[{"w":"umbrella stand","p":123456,"f":[0.41,0.62],"o":[0.5,0.5]},
      {"w":"wheelbarrow","u":"sessions/<sid>/uploads/a.jpg","f":[0.5,0.5]}]}
p = Pixabay photo id, u = uploaded path on the state branch (exactly one).
f = optional arrow target; without it the video is rendered without the arrow.
x = optional list of skipped words (they join bad_words.txt after a good build).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import errors as E
from .words import normalize

SID_RE = re.compile(r"^[A-Za-z0-9_-]{6,48}$")
MAX_CHARS = 60000


@dataclass
class Pick:
    word: str
    photo_id: int | None
    upload: str | None
    focus: tuple | None    # None = no arrow (the user did not tap the picture)
    offset: tuple


@dataclass
class Selection:
    sid: str
    theme: str
    picks: list
    skipped: list = None   # words the user skipped (added to bad_words after a good build)


def _frac(v, name):
    try:
        a, b = float(v[0]), float(v[1])
    except Exception:
        raise E.WCError(E.SELECTION_BAD, f"{name} must be [x,y]", "selection")
    if not (0.0 <= a <= 1.0 and 0.0 <= b <= 1.0):
        raise E.WCError(E.SELECTION_BAD, f"{name} must be in 0..1", "selection")
    return (a, b)


def parse_selection(text: str, expect_words: int | None = 3) -> Selection:
    if len(text) > MAX_CHARS:
        raise E.WCError(E.SELECTION_BAD, "selection too long", "selection")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as ex:
        raise E.WCError(E.SELECTION_BAD, f"invalid JSON: {ex}", "selection")
    if not isinstance(d, dict) or d.get("v") != 1:
        raise E.WCError(E.SELECTION_BAD, "unsupported version", "selection")
    sid = str(d.get("sid", ""))
    if not SID_RE.match(sid):
        raise E.WCError(E.SELECTION_BAD, "bad session id", "selection")
    items = d.get("w")
    if not isinstance(items, list) or not items:
        raise E.WCError(E.SELECTION_BAD, "no words", "selection")
    if expect_words and len(items) != expect_words:
        raise E.WCError(E.SELECTION_BAD,
                        f"expected {expect_words} words, got {len(items)}", "selection")
    picks, seen = [], set()
    for it in items:
        word = " ".join(re.findall(r"[a-z]+", str(it.get("w", "")).lower()))
        if not word:
            raise E.WCError(E.SELECTION_BAD, "empty word", "selection")
        n = normalize(word)
        if n in seen:
            raise E.WCError(E.SELECTION_BAD, f"duplicate word: {word}", "selection")
        seen.add(n)
        p, u = it.get("p"), it.get("u")
        if (p is None) == (u is None):
            raise E.WCError(E.SELECTION_BAD, f"{word}: need exactly one of p/u", "selection")
        if u is not None:
            u = str(u)
            if ".." in u or not u.startswith(f"sessions/{sid}/uploads/"):
                raise E.WCError(E.SELECTION_BAD, f"{word}: bad upload path", "selection")
        if p is not None:
            try:
                p = int(p)
            except (TypeError, ValueError):
                raise E.WCError(E.SELECTION_BAD, f"{word}: bad photo id", "selection")
        focus = _frac(it["f"], "f") if it.get("f") is not None else None
        picks.append(Pick(word, p, u, focus, _frac(it.get("o", [0.5, 0.5]), "o")))
    theme = str(d.get("theme", "auto"))
    skipped = []
    for x in (d.get("x") or [])[:30]:
        w = " ".join(re.findall(r"[a-z]+", str(x).lower()))
        if w and w not in skipped:
            skipped.append(w)
    return Selection(sid, theme, picks, skipped)
