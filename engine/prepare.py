"""prepare.yml / more_images.yml entry point.

  python -m engine.prepare --session SID [--category kitchen]
  python -m engine.prepare --session SID --more "wood chisel"
  python -m engine.prepare --session SID --refill --exclude "word one,word two"

--refill adds fresh words to an existing session (used when the user skipped
every spare word), so "skip" never runs out.

Writes sessions/<SID>/candidates.json and status.json on the state branch.
On failure status.json carries a stable error code the UI translates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import traceback

from . import config as C
from . import errors as E
from .pixabay import Pixabay, photo_keys, unique_photos
from .selection import SID_RE
from . import vision as V
from .state import State
from .words import (builtin_words, fallback_words, filter_candidates, groq_words,
                    normalize)

PHOTOS_PER_WORD = 4
VISION_POOL = 10          # photos fetched per word so that 4 can survive the vision check
VISION_PAUSE = 2.1        # seconds between vision calls (Groq free tier ~30 requests/minute)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vision_on(settings, mock: bool) -> bool:
    return bool(getattr(settings, "vision_check", True)) and not mock \
        and bool(os.environ.get("GROQ_API_KEY", "").strip())


def vision_filter(settings, word: str, photos: list, keep: int, warnings: list) -> list:
    """Keep only photos the vision model says show `word`. Never raises."""
    kept, rep = V.filter_photos(word, photos, keep, settings.vision_model, pause=VISION_PAUSE)
    print(f"[prepare] vision '{word}': {rep}", flush=True)
    if rep["unavailable"] and not any(w.get("code") == "vision_unavailable" for w in warnings):
        warnings.append({"code": "vision_unavailable", "stage": "vision",
                         "detail": "vision model could not be used; photos were NOT checked"})
    return kept


def pick_words(settings, used, bad, category, rng, mock, total) -> tuple:
    """Return (candidate list ordered by preference, source, warnings)."""
    warnings = []
    source = "groq"
    items = []
    if mock:
        source = "builtin"
        items = filter_candidates(builtin_words(rng, category), used, bad)
    else:
        try:
            raw = groq_words(total, used, bad, category, settings.text_model)
            items = filter_candidates(raw, used, bad)
        except E.WCError as ex:
            print(f"[prepare] WARNING Groq text failed ({ex.code}): {ex.detail[:200]}", flush=True)
            warnings.append({"code": ex.code, "detail": ex.detail[:200], "stage": "groq"})
            fb, source = fallback_words(rng, category)
            items = filter_candidates(fb, used, bad)
    return items, source, warnings


def build_candidates(settings, state: State, sid: str, category: str | None,
                     mock: bool, extra_used=(), total: int | None = None,
                     seed: str = "") -> dict:
    """Pick words + photos. `used_words.txt` (the word history on the state branch)
    is read here, BEFORE any word is chosen, so a word that was already part of a
    finished video is never offered again. `extra_used` = more words to avoid
    (the ones already in this session / skipped)."""
    used = state.read_lines("used_words.txt") + [w for w in extra_used if w]
    bad = state.read_lines("bad_words.txt")
    rng = random.Random(sid + seed)
    total = total or settings.words_per_video * 2
    items, source, warnings = pick_words(settings, used, bad, category, rng, mock, total)
    px = Pixabay(mock=mock)
    use_vision = vision_on(settings, mock)
    pool = VISION_POOL if use_vision else PHOTOS_PER_WORD
    out, seen = [], set()
    topped_up = False
    queue = list(items)
    while queue and len(out) < total:
        it = queue.pop(0)
        n = normalize(it["word"])
        if n in seen:
            continue
        try:
            photos = px.candidates(it["word"], it["search_query"], page=1, want=pool)
            if use_vision and len(photos) >= PHOTOS_PER_WORD:
                photos = vision_filter(settings, it["word"], photos, PHOTOS_PER_WORD, warnings)
        except E.WCError as ex:
            if ex.code in (E.PIXABAY_NO_KEY, E.PIXABAY_RATE):
                raise
            print(f"[prepare] Pixabay failed for {it['word']}: {ex.detail}", flush=True)
            continue
        if len(photos) < PHOTOS_PER_WORD:
            print(f"[prepare] drop '{it['word']}': only {len(photos)} usable photos", flush=True)
            continue
        seen.add(n)
        out.append({**it, "photos": photos, "page": 1})
        if not queue and len(out) < total and not topped_up:
            topped_up = True
            extra, _src = fallback_words(rng, category)
            more = filter_candidates(extra, used + [o["word"] for o in out] + [x["word"] for x in queue], bad)
            queue.extend(more[:20])
            warnings.append({"code": "topped_up", "detail": "extra words added from fallback list",
                             "stage": "words"})
    need = min(settings.words_per_video, total)
    if len(out) < need:
        raise E.WCError(E.PIXABAY_EMPTY,
                        f"only {len(out)} usable words found (need {need})",
                        "prepare")
    return {
        "session": sid,
        "created": _now(),
        "category": category or "",
        "source": source,
        "words_per_video": settings.words_per_video,
        "warnings": warnings,
        "words": out,
    }


def more_images(state: State, sid: str, word: str, mock: bool, settings=None) -> dict:
    path = f"sessions/{sid}/candidates.json"
    data = state.read_json(path)
    if not data:
        raise E.WCError(E.IMAGE_MISSING, "no candidates.json for this session", "more_images")
    target = None
    for w in data["words"]:
        if w["word"] == word or normalize(w["word"]) == normalize(word):
            target = w
            break
    if target is None:
        raise E.WCError(E.SELECTION_BAD, f"unknown word '{word}'", "more_images")
    px = Pixabay(mock=mock)
    use_vision = settings is not None and vision_on(settings, mock)
    have = {p["id"] for p in target["photos"]}
    have_keys = set()
    for p in target["photos"]:
        have_keys |= photo_keys(p)
    added = []
    page = target.get("page", 1)
    for _ in range(3):
        page += 1
        found = px.candidates(target["word"], target["search_query"], page=page,
                              want=VISION_POOL if use_vision else PHOTOS_PER_WORD, exclude_ids=have,
                              exclude_keys=have_keys)
        if use_vision and found:
            found = vision_filter(settings, target["word"], found, PHOTOS_PER_WORD, [])
        for p in unique_photos(found, have_keys):
            have.add(p["id"])
            have_keys |= photo_keys(p)
            added.append(p)
        target["page"] = page
        if len(added) >= PHOTOS_PER_WORD:
            break
    if not added:
        raise E.WCError(E.PIXABAY_EMPTY, f"no more photos for '{word}'", "more_images")
    target["photos"].extend(added[:PHOTOS_PER_WORD])
    data["updated"] = _now()
    state.write_json(path, data)
    return {"word": target["word"], "added": len(added[:PHOTOS_PER_WORD])}


def refill_words(settings, state: State, sid: str, exclude: list, mock: bool) -> dict:
    """Append new words (with photos) to an existing session. Never repeats a word
    that is already in the session, was skipped, or is in the word history."""
    path = f"sessions/{sid}/candidates.json"
    data = state.read_json(path)
    if not data:
        raise E.WCError(E.IMAGE_MISSING, "no candidates.json for this session", "refill")
    have = [w["word"] for w in data["words"]]
    more = build_candidates(settings, state, sid, data.get("category") or None, mock,
                            extra_used=have + list(exclude), total=settings.words_per_video,
                            seed=f"-refill{len(have)}")
    data["words"].extend(more["words"])
    data["warnings"] = (data.get("warnings") or []) + more["warnings"]
    data["updated"] = _now()
    state.write_json(path, data)
    return {"added": [w["word"] for w in more["words"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--category", default="")
    ap.add_argument("--more", default="")
    ap.add_argument("--refill", action="store_true")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "state"))
    ap.add_argument("--remote", default=os.environ.get("STATE_REMOTE", ""))
    a = ap.parse_args(argv)
    if not SID_RE.match(a.session):
        print("bad session id", file=sys.stderr)
        return 2
    settings = C.load_settings()
    state = State(a.state_dir, a.remote or None).init()
    base = f"sessions/{a.session}"
    try:
        if a.refill:
            excl = [w.strip().lower() for w in a.exclude.split(",") if w.strip()]
            res = refill_words(settings, state, a.session, excl, a.mock)
            state.write_json(f"{base}/more_status.json",
                             {"state": "ok", "refill": res["added"], "at": _now()})
            msg = f"refill {a.session}"
        elif a.more:
            res = more_images(state, a.session, a.more, a.mock, settings)
            state.write_json(f"{base}/more_status.json",
                             {"state": "ok", "word": res["word"], "added": res["added"],
                              "at": _now()})
            msg = f"more images {a.session} {res['word']}"
        else:
            cand = build_candidates(settings, state, a.session, a.category or None, a.mock)
            state.write_json(f"{base}/candidates.json", cand)
            state.write_json(f"{base}/status.json",
                             {"state": "ok", "source": cand["source"],
                              "warnings": cand["warnings"], "at": _now()})
            removed = state.prune_sessions()
            if removed:
                print(f"[prepare] pruned old sessions: {removed}")
            msg = f"prepare {a.session}"
        state.commit_push(msg)
        print("[prepare] done")
        return 0
    except E.WCError as ex:
        print(f"[prepare] FAILED {ex}", file=sys.stderr)
        name = "more_status.json" if (a.more or a.refill) else "status.json"
        state.write_json(f"{base}/{name}", {**ex.to_dict(), "at": _now()})
        try:
            state.commit_push(f"{name} error {a.session}")
        except E.WCError:
            pass
        return 1
    except Exception as ex:  # noqa: BLE001
        traceback.print_exc()
        name = "more_status.json" if (a.more or a.refill) else "status.json"
        state.write_json(f"{base}/{name}",
                         {"state": "error", "code": E.UNKNOWN, "detail": str(ex)[:300],
                          "stage": "prepare", "at": _now()})
        try:
            state.commit_push(f"{name} error {a.session}")
        except E.WCError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
