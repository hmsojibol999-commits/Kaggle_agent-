"""prepare.yml / more_images.yml entry point.

  python -m engine.prepare --session SID [--category kitchen]
  python -m engine.prepare --session SID --more "wood chisel"

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
from .pexels import Pexels
from .selection import SID_RE
from .state import State
from .words import (builtin_words, fallback_words, filter_candidates, groq_words,
                    normalize)

PHOTOS_PER_WORD = 4


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                     mock: bool) -> dict:
    used = state.read_lines("used_words.txt")
    bad = state.read_lines("bad_words.txt")
    rng = random.Random(sid)
    total = settings.words_per_video * 2
    items, source, warnings = pick_words(settings, used, bad, category, rng, mock, total)
    px = Pexels(mock=mock)
    out, seen = [], set()
    topped_up = False
    queue = list(items)
    while queue and len(out) < total:
        it = queue.pop(0)
        n = normalize(it["word"])
        if n in seen:
            continue
        try:
            photos = px.candidates(it["word"], it["search_query"], page=1, want=PHOTOS_PER_WORD)
        except E.WCError as ex:
            if ex.code in (E.PEXELS_NO_KEY, E.PEXELS_RATE):
                raise
            print(f"[prepare] Pexels failed for {it['word']}: {ex.detail}", flush=True)
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
    if len(out) < settings.words_per_video:
        raise E.WCError(E.PEXELS_EMPTY,
                        f"only {len(out)} usable words found (need {settings.words_per_video})",
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


def more_images(state: State, sid: str, word: str, mock: bool) -> dict:
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
    px = Pexels(mock=mock)
    have = {p["id"] for p in target["photos"]}
    added = []
    page = target.get("page", 1)
    for _ in range(3):
        page += 1
        found = px.candidates(target["word"], target["search_query"], page=page,
                              want=PHOTOS_PER_WORD, exclude_ids=have)
        for p in found:
            if p["id"] not in have:
                have.add(p["id"])
                added.append(p)
        target["page"] = page
        if len(added) >= PHOTOS_PER_WORD:
            break
    if not added:
        raise E.WCError(E.PEXELS_EMPTY, f"no more photos for '{word}'", "more_images")
    target["photos"].extend(added[:PHOTOS_PER_WORD])
    data["updated"] = _now()
    state.write_json(path, data)
    return {"word": target["word"], "added": len(added[:PHOTOS_PER_WORD])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--category", default="")
    ap.add_argument("--more", default="")
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
        if a.more:
            res = more_images(state, a.session, a.more, a.mock)
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
        name = "more_status.json" if a.more else "status.json"
        state.write_json(f"{base}/{name}", {**ex.to_dict(), "at": _now()})
        try:
            state.commit_push(f"{name} error {a.session}")
        except E.WCError:
            pass
        return 1
    except Exception as ex:  # noqa: BLE001
        traceback.print_exc()
        name = "more_status.json" if a.more else "status.json"
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
