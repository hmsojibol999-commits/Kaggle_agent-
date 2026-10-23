"""Pixabay search: quality filter, scoring and candidate extraction.

Photos come from Pixabay only (https://pixabay.com/api/docs/). Attribution
(contributor + Pixabay page link) is kept in every candidate so build.py can
write credits.json and release notes.

What is different from a typical photo API, and handled here:
* the API key travels in the URL, so it is scrubbed from every error text
  (error details are written to the public state branch);
* `largeImageURL` is scaled to at most 1280 px (the original needs "full API
  access"), so sizes are judged on what can really be downloaded;
* image links expire after about 24 hours: `Pixabay.by_id` returns fresh ones;
* the limit is 100 requests per 60 seconds per key (HTTP 429).
"""
from __future__ import annotations

import os
import re
import time
import zlib
from urllib.parse import quote

import requests

from . import errors as E
from . import geometry as G

API = "https://pixabay.com/api/"
PER_PAGE = 30
MAX_SIDE = 1280        # largeImageURL: max width/height
FULL_HD_SIDE = 1920    # fullHDURL (only with full API access)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) WordClipStudio/1.0"

# Stepwise relaxation: the biggest enlargement allowed for the downloadable image
# to cover the card (1.0 = exactly card size, see geometry.cover_scale).
RES_LEVELS = [1.10, 1.30, 1.50]

PERSON_WORDS = {
    "man", "men", "woman", "women", "girl", "boy", "person", "people", "child",
    "children", "kid", "kids", "couple", "portrait", "model", "lady", "guy",
    "baby", "family", "friends", "smiling", "wearing", "holding", "standing",
    "sitting", "walking", "worker", "student", "chef", "farmer",
}


def _tokens(s: str) -> list:
    return re.findall(r"[a-z]+", (s or "").lower())


def score_photo(photo: dict, word: str) -> float:
    """Higher is better. Soft penalties only; hard filtering happens elsewhere.
    `alt` holds the Pixabay tags ("umbrella, rain, protection")."""
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


def _download_size(hit: dict) -> tuple:
    """Pixel size of the biggest image we can really download for this hit."""
    w, h = int(hit.get("imageWidth") or 0), int(hit.get("imageHeight") or 0)
    if hit.get("imageURL"):                 # full API access: the original file
        return w, h
    cap = FULL_HD_SIDE if hit.get("fullHDURL") else MAX_SIDE
    big = max(w, h)
    if big > cap:
        k = cap / big
        w, h = round(w * k), round(h * k)
    return w, h


def _normalize(hit: dict) -> dict | None:
    """One raw Pixabay hit -> the internal photo dict (None when unusable)."""
    try:
        pid = int(hit["id"])
        w, h = _download_size(hit)
    except (KeyError, TypeError, ValueError):
        return None
    large = hit.get("largeImageURL") or ""
    if w <= 0 or h <= 0 or not large:
        return None
    user, uid = hit.get("user") or "", hit.get("user_id")
    return {
        "id": pid,
        "width": w,
        "height": h,
        "alt": hit.get("tags") or "",
        "photographer": user,
        "photographer_url": f"https://pixabay.com/users/{quote(user)}-{uid}/" if user and uid else "",
        "url": hit.get("pageURL") or "",
        "src": {"medium": hit.get("webformatURL") or "",
                "large": large,
                "original": hit.get("imageURL") or hit.get("fullHDURL") or large},
    }


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
        "large": src.get("large") or "",
        "original": src.get("original") or src.get("large") or "",
        "score": round(score_photo(photo, word), 3),
    }


def filter_and_rank(photos: list, word: str, want: int = 4, exclude_ids=()) -> list:
    """Apply stepwise resolution filter, rank, return up to `want` slim dicts."""
    ex = set(exclude_ids)
    pool = [p for p in photos if p.get("id") not in ex and p.get("width") and p.get("height")]
    chosen = []
    for max_scale in RES_LEVELS:
        ok = [p for p in pool if G.cover_scale(p["width"], p["height"]) <= max_scale]
        if len(ok) >= want:
            chosen = ok
            break
        if len(ok) > len(chosen):
            chosen = ok
    ranked = sorted(chosen, key=lambda p: score_photo(p, word), reverse=True)
    return [_slim(p, word) for p in ranked[:want]]


class Pixabay:
    def __init__(self, key: str | None = None, mock: bool = False):
        self.key = key if key is not None else os.environ.get("PIXABAY_API_KEY", "").strip()
        self.mock = mock

    def _redact(self, text) -> str:
        """The key is part of the URL; never let it reach logs or status.json."""
        text = str(text)
        return text.replace(self.key, "***") if self.key else text

    def _request(self, params: dict) -> list:
        """One API call with retries; returns the raw `hits` list."""
        if not self.key:
            raise E.WCError(E.PIXABAY_NO_KEY, "PIXABAY_API_KEY is empty", "pixabay")
        last, rated = "", False
        for attempt in range(3):
            rated = False
            try:
                r = requests.get(API, params={"key": self.key, **params},
                                 headers={"User-Agent": USER_AGENT}, timeout=30)
            except requests.RequestException as ex:
                last = self._redact(ex)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                try:
                    return r.json().get("hits", [])
                except ValueError:
                    last = "answer was not JSON"
                    time.sleep(2 ** attempt)
                    continue
            body = (getattr(r, "text", "") or "")[:200]
            low = body.lower()
            if r.status_code in (401, 403) or (r.status_code == 400 and "key" in low):
                raise E.WCError(E.PIXABAY_NO_KEY, f"HTTP {r.status_code}: key rejected", "pixabay")
            if r.status_code == 400:
                if "page" in low:           # '"page" is out of valid range': nothing more to show
                    return []
                last = f"HTTP 400: {self._redact(body)}"
                break                       # a bad request will not get better by retrying
            if r.status_code == 429:
                last, rated = "rate limited", True
                time.sleep(_wait_seconds(r, attempt))
                continue
            last = f"HTTP {r.status_code}"
            time.sleep(2 ** attempt)
        raise E.WCError(E.PIXABAY_RATE if rated else E.PIXABAY_EMPTY, last, "pixabay")

    def search_raw(self, query: str, page: int = 1, per_page: int = PER_PAGE) -> list:
        """One page of search results as internal photo dicts (see _normalize)."""
        if self.mock:
            hits = _mock_results(query, page, per_page)
        else:
            hits = self._request({"q": (query or "")[:100], "image_type": "photo",
                                  "safesearch": "true", "page": page,
                                  "per_page": max(3, min(per_page, 200))})
        return [p for p in (_normalize(h) for h in hits) if p]

    def candidates(self, word: str, query: str, page: int = 1, want: int = 4,
                   exclude_ids=()) -> list:
        q = (query or word).strip()
        out = filter_and_rank(self.search_raw(q, page=page), word, want=want,
                              exclude_ids=exclude_ids)
        w = (word or "").strip()
        if len(out) < want and w and w.lower() != q.lower():
            # Pixabay wants every query word among the tags, so a long query can be
            # too narrow: top up with a search for the bare word.
            skip = set(exclude_ids) | {p["id"] for p in out}
            out += filter_and_rank(self.search_raw(w, page=page), word,
                                   want=want - len(out), exclude_ids=skip)
        return out

    def by_id(self, photo_id: int) -> dict | None:
        """Fresh image links for one photo (slim dict), or None when not found."""
        if self.mock:
            return None
        pid = int(photo_id)
        for p in (_normalize(h) for h in self._request({"id": pid})):
            if p and p["id"] == pid:
                return _slim(p, "")
        return None


def _wait_seconds(resp, attempt: int) -> int:
    """How long to wait after HTTP 429: Pixabay says when the 60 s window resets."""
    try:
        reset = int((getattr(resp, "headers", None) or {}).get("X-RateLimit-Reset", ""))
        return max(2, min(reset + 1, 65))
    except (TypeError, ValueError):
        return 2 ** (attempt + 1)


def _mock_results(query: str, page: int, per_page: int) -> list:
    """Deterministic fake Pixabay answer (raw `hits`) for --mock (no network)."""
    base = zlib.crc32(query.encode()) % 100000
    out = []
    for i in range(per_page):
        pid = base * 100 + (page - 1) * 50 + i + 1
        out.append({
            "id": pid,
            "pageURL": f"https://pixabay.com/photos/mock-{pid}/",
            "type": "photo",
            "tags": f"{query}, table, still life" if i % 4 else f"woman, holding, {query}",
            "webformatURL": f"mock://{pid}",
            "largeImageURL": f"mock://{pid}",
            "imageWidth": 4000 if i % 3 else 2400,
            "imageHeight": 3000 if i % 3 else 2400,
            "user": f"Mock-Photographer-{i}",
            "user_id": 1000 + i,
        })
    return out
