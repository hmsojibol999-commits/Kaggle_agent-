"""Pexels search: quality filter, scoring and candidate extraction.

Photos come from Pexels only. Attribution (photographer + Pexels link) is kept
in every candidate so build.py can write credits.json and release notes.
"""
from __future__ import annotations

import os
import re
import time
import zlib

import requests

from . import errors as E

API = "https://api.pexels.com/v1/search"

# stepwise relaxation of the minimum source size
RES_LEVELS = [(1600, 1200), (1300, 1000), (1000, 1000)]

PERSON_WORDS = {
    "man", "men", "woman", "women", "girl", "boy", "person", "people", "child",
    "children", "kid", "kids", "couple", "portrait", "model", "lady", "guy",
    "baby", "family", "friends", "smiling", "wearing", "holding", "standing",
    "sitting", "walking", "worker", "student", "chef", "farmer",
}


def _tokens(s: str) -> list:
    return re.findall(r"[a-z]+", (s or "").lower())


def score_photo(photo: dict, word: str) -> float:
    """Higher is better. Soft penalties only; hard filtering happens elsewhere."""
    alt = (photo.get("alt") or "").lower()
    toks = set(_tokens(alt))
    w = word.lower().strip()
    score = 0.0
    if w and w in alt:
        score += 3.0
    wt = _tokens(w)
    if wt:
        score += 1.5 * sum(1 for t in wt if t in toks) / len(wt)
    if toks & PERSON_WORDS:
        score -= 2.0
    pw, ph = photo.get("width", 0), photo.get("height", 1)
    aspect = pw / max(ph, 1)
    if 0.9 <= aspect <= 1.8:
        score += 1.0           # landscape or square preferred
    elif aspect < 0.7:
        score -= 0.5
    score += min(pw, 6000) / 6000.0  # tiny bonus for resolution
    return score


def _slim(photo: dict, word: str) -> dict:
    src = photo.get("src", {})
    return {
        "id": photo["id"],
        "w": photo["width"],
        "h": photo["height"],
        "alt": (photo.get("alt") or "")[:140],
        "photographer": photo.get("photographer", ""),
        "photographer_url": photo.get("photographer_url", ""),
        "url": photo.get("url", ""),
        "thumb": src.get("medium") or src.get("large") or "",
        "large": src.get("large2x") or src.get("large") or "",
        "original": src.get("original") or src.get("large2x") or "",
        "score": round(score_photo(photo, word), 3),
    }


def filter_and_rank(photos: list, word: str, want: int = 4, exclude_ids=()) -> list:
    """Apply stepwise resolution filter, rank, return up to `want` slim dicts."""
    ex = set(exclude_ids)
    pool = [p for p in photos if p.get("id") not in ex and "width" in p]
    chosen = []
    for min_w, min_h in RES_LEVELS:
        ok = [p for p in pool if p["width"] >= min_w and p["height"] >= min_h]
        if len(ok) >= want:
            chosen = ok
            break
        if len(ok) > len(chosen):
            chosen = ok
    ranked = sorted(chosen, key=lambda p: score_photo(p, word), reverse=True)
    return [_slim(p, word) for p in ranked[:want]]


class Pexels:
    def __init__(self, key: str | None = None, mock: bool = False):
        self.key = key if key is not None else os.environ.get("PEXELS_API_KEY", "")
        self.mock = mock

    def search_raw(self, query: str, page: int = 1, per_page: int = 15) -> list:
        if self.mock:
            return _mock_results(query, page, per_page)
        if not self.key:
            raise E.WCError(E.PEXELS_NO_KEY, "PEXELS_API_KEY is empty", "pexels")
        last = ""
        for attempt in range(3):
            try:
                r = requests.get(
                    API,
                    headers={"Authorization": self.key},
                    params={"query": query, "per_page": per_page, "page": page},
                    timeout=30,
                )
            except requests.RequestException as ex:
                last = str(ex)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json().get("photos", [])
            if r.status_code in (401, 403):
                raise E.WCError(E.PEXELS_NO_KEY, f"HTTP {r.status_code}: key rejected", "pexels")
            if r.status_code == 429:
                last = "rate limited"
                time.sleep(2 ** (attempt + 1))
                continue
            last = f"HTTP {r.status_code}"
            time.sleep(2 ** attempt)
        code = E.PEXELS_RATE if "rate" in last else E.PEXELS_EMPTY
        raise E.WCError(code, last, "pexels")

    def candidates(self, word: str, query: str, page: int = 1, want: int = 4,
                   exclude_ids=()) -> list:
        raw = self.search_raw(query or word, page=page, per_page=15)
        return filter_and_rank(raw, word, want=want, exclude_ids=exclude_ids)


def _mock_results(query: str, page: int, per_page: int) -> list:
    """Deterministic fake Pexels answer for --mock (no network)."""
    base = zlib.crc32(query.encode()) % 100000
    out = []
    for i in range(per_page):
        pid = base * 100 + (page - 1) * 50 + i + 1
        out.append({
            "id": pid,
            "width": 4000 if i % 3 else 2400,
            "height": 3000 if i % 3 else 2400,
            "url": f"https://www.pexels.com/photo/{pid}/",
            "photographer": f"Mock Photographer {i}",
            "photographer_url": "https://www.pexels.com/@mock",
            "alt": f"{query} on a table" if i % 4 else "woman holding " + query,
            "src": {"original": f"mock://{pid}", "large2x": f"mock://{pid}",
                    "large": f"mock://{pid}", "medium": f"mock://{pid}"},
        })
    return out
