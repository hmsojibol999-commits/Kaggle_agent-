"""Word generation (Groq text model) + validation helpers + fallbacks."""
from __future__ import annotations

import json
import os
import random
import re
import time

import requests

from . import errors as E

GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"

AMBIGUOUS = {
    "bat", "crane", "seal", "bark", "bow", "date", "fan", "file", "jam", "lead",
    "mouse", "nail", "novel", "pen", "pitcher", "plant", "ring", "rock", "saw",
    "spring", "star", "stick", "tank", "tire", "watch", "wave", "light", "match",
}

BUILTIN_WORDS = [
    ("umbrella stand", "umbrella stand", "home", 2), ("wheelbarrow", "wheelbarrow garden", "garden", 2),
    ("wood chisel", "wood chisel tool", "tools", 2), ("colander", "colander kitchen", "kitchen", 2),
    ("ladle", "ladle spoon kitchen", "kitchen", 2), ("whisk", "whisk kitchen utensil", "kitchen", 1),
    ("spatula", "spatula kitchen", "kitchen", 1), ("tongs", "kitchen tongs", "kitchen", 2),
    ("rolling pin", "rolling pin dough", "kitchen", 1), ("kettle", "kettle stove", "kitchen", 1),
    ("teapot", "teapot ceramic", "kitchen", 1), ("toaster", "toaster bread", "kitchen", 1),
    ("blender", "blender kitchen appliance", "kitchen", 1), ("grater", "cheese grater", "kitchen", 2),
    ("hammer", "hammer tool", "tools", 1), ("screwdriver", "screwdriver tool", "tools", 1),
    ("wrench", "wrench spanner tool", "tools", 1), ("pliers", "pliers tool", "tools", 2),
    ("tape measure", "tape measure tool", "tools", 2), ("paintbrush", "paintbrush", "tools", 1),
    ("ladder", "ladder wooden", "tools", 1), ("shovel", "shovel garden tool", "garden", 1),
    ("rake", "garden rake", "garden", 1), ("watering can", "watering can garden", "garden", 1),
    ("flowerpot", "flower pot terracotta", "garden", 1), ("garden hose", "garden hose coiled", "garden", 2),
    ("scissors", "scissors paper", "office", 1), ("stapler", "stapler office", "office", 1),
    ("paper clip", "paper clips", "office", 1), ("globe", "desk globe", "office", 1),
    ("calculator", "calculator desk", "office", 1), ("backpack", "backpack school", "clothing", 1),
    ("sneakers", "sneakers shoes", "clothing", 1), ("scarf", "knitted scarf", "clothing", 1),
    ("gloves", "winter gloves", "clothing", 1), ("hanger", "clothes hanger", "clothing", 1),
    ("toothbrush", "toothbrush bathroom", "bathroom", 1), ("soap dispenser", "soap dispenser", "bathroom", 2),
    ("towel rack", "towel rack bathroom", "bathroom", 2), ("bathtub", "bathtub white", "bathroom", 1),
    ("armchair", "armchair living room", "furniture", 1), ("bookshelf", "bookshelf books", "furniture", 1),
    ("stool", "wooden stool", "furniture", 1), ("lamp", "table lamp", "furniture", 1),
    ("candle", "candle flame", "home", 1), ("mirror", "wall mirror", "home", 1),
    ("padlock", "padlock", "home", 2), ("doorbell", "doorbell", "home", 2),
    ("bicycle", "bicycle parked", "vehicles", 1), ("skateboard", "skateboard", "sports", 1),
    ("football", "football ball", "sports", 1), ("tennis racket", "tennis racket", "sports", 2),
    ("dumbbell", "dumbbell gym", "sports", 1), ("helmet", "bicycle helmet", "sports", 1),
    ("teddy bear", "teddy bear toy", "toys", 1), ("puzzle", "jigsaw puzzle", "toys", 1),
    ("headphones", "headphones", "electronics", 1), ("keyboard", "computer keyboard", "electronics", 1),
    ("camera", "camera lens", "electronics", 1), ("compass", "compass navigation", "tools", 2),
    ("hourglass", "hourglass sand", "home", 2), ("binoculars", "binoculars", "outdoors", 2),
    ("lantern", "lantern", "outdoors", 2), ("pineapple", "pineapple fruit", "food", 1),
    ("pomegranate", "pomegranate fruit", "food", 2), ("broccoli", "broccoli", "food", 1),
]


# ------------------------------------------------------------- normalisation
_IRREG = {"knives": "knife", "leaves": "leaf", "shelves": "shelf", "glasses": "glass",
          "scissors": "scissors", "pliers": "pliers", "tongs": "tongs", "gloves": "glove",
          "shoes": "shoe", "sneakers": "sneaker", "headphones": "headphone",
          "binoculars": "binocular", "pants": "pant", "jeans": "jean"}


def singular(token: str) -> str:
    t = token.lower()
    if t in _IRREG:
        return _IRREG[t]
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if re.search(r"(ses|xes|zes|ches|shes)$", t) and len(t) > 4:
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        return t[:-1]
    return t


def normalize(word: str) -> str:
    """Lowercase, strip punctuation, singularise every token."""
    toks = re.findall(r"[a-z]+", (word or "").lower())
    return " ".join(singular(t) for t in toks)


def clean_word(word: str) -> str:
    return " ".join(re.findall(r"[a-z]+", (word or "").lower()))


def is_valid_shape(word: str) -> bool:
    w = clean_word(word)
    if not w or len(w) < 3 or len(w) > 24:
        return False
    if len(w.split()) > 2:
        return False
    if any(t in AMBIGUOUS for t in w.split()):
        return False
    return True


def read_list(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def filter_candidates(items: list, used: list, bad: list) -> list:
    """Drop invalid / used / bad / duplicate-after-normalisation words."""
    blocked = {normalize(w) for w in list(used) + list(bad)}
    seen = set()
    out = []
    for it in items:
        w = clean_word(it.get("word", ""))
        if not is_valid_shape(w):
            continue
        n = normalize(w)
        if n in blocked or n in seen:
            continue
        seen.add(n)
        it = dict(it)
        it["word"] = w
        it["search_query"] = (it.get("search_query") or w).strip().lower()
        it["category"] = (it.get("category") or "misc").strip().lower()
        try:
            it["difficulty"] = int(it.get("difficulty", 2))
        except (TypeError, ValueError):
            it["difficulty"] = 2
        it["difficulty"] = min(3, max(1, it["difficulty"]))
        out.append(it)
    return out


# ------------------------------------------------------------------ groq
PROMPT = """You create vocabulary words for a kids/learners English video called \
"What's this called?". Return ONLY a JSON object: {{"words": [ ... ]}} with exactly {n} items. \
Each item: {{"word": str, "search_query": str, "category": str, "difficulty": 1-3}}.
Rules:
- common, concrete, single physical objects that are easy to photograph on their own
- 1 or 2 words, lowercase, singular unless naturally plural (scissors)
- no brands, no people, no animals-in-action, no abstract words
- avoid ambiguous words (bat, crane, seal, bark, ring, watch, mouse, date...)
- search_query must disambiguate for a stock-photo search, e.g. "wood chisel tool"
- mix categories{cat_hint}
- NEVER use any of these words: {blocked}
"""


def _groq_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _classify_groq_error(status: int, body: str) -> E.WCError:
    low = (body or "").lower()
    if "terms" in low and ("accept" in low or "required" in low):
        return E.WCError(E.GROQ_TERMS, body[:300], "groq")
    if status in (401,):
        return E.WCError(E.GROQ_NO_KEY, body[:300], "groq")
    if status in (403, 404) or "model_not_found" in low or "decommissioned" in low:
        return E.WCError(E.GROQ_MODEL, body[:300], "groq")
    if status == 429:
        return E.WCError(E.GROQ_RATE, body[:300], "groq")
    return E.WCError(E.GROQ_BAD, f"HTTP {status}: {body[:300]}", "groq")


def groq_words(n: int, used: list, bad: list, category: str | None, model: str,
               key: str | None = None) -> list:
    key = key if key is not None else os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise E.WCError(E.GROQ_NO_KEY, "GROQ_API_KEY is empty", "groq")
    blocked = ", ".join(sorted(set((list(used) + list(bad))[-300:])))
    cat_hint = f" (focus on: {category})" if category else ""
    prompt = PROMPT.format(n=n + 4, blocked=blocked or "(none)", cat_hint=cat_hint)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "response_format": {"type": "json_object"},
    }
    last_err: E.WCError | None = None
    for attempt in range(3):
        try:
            r = requests.post(GROQ_CHAT, headers=_groq_headers(key), json=body, timeout=60)
        except requests.RequestException as ex:
            last_err = E.WCError(E.GROQ_BAD, str(ex), "groq")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 400 and "response_format" in r.text.lower():
            body.pop("response_format", None)      # model without JSON mode
            continue
        if r.status_code != 200:
            last_err = _classify_groq_error(r.status_code, r.text)
            if last_err.code in (E.GROQ_NO_KEY, E.GROQ_MODEL, E.GROQ_TERMS):
                raise last_err                     # retrying cannot help
            time.sleep(2 ** (attempt + 1))
            continue
        try:
            text = r.json()["choices"][0]["message"]["content"]
            return parse_words_json(text)
        except Exception as ex:                    # parse failure -> retry
            last_err = E.WCError(E.GROQ_BAD, f"parse: {ex}", "groq")
            time.sleep(1)
    raise last_err or E.WCError(E.GROQ_BAD, "unknown", "groq")


def parse_words_json(text: str) -> list:
    """Parse model output robustly (code fences, extra prose, bare list)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", t, flags=re.S)
        if not m:
            raise ValueError("no JSON found")
        data = json.loads(m.group(1))
    if isinstance(data, dict):
        for k in ("words", "items", "data"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list) or not data:
        raise ValueError("empty list")
    out = []
    for x in data:
        if isinstance(x, str):
            x = {"word": x}
        if isinstance(x, dict) and x.get("word"):
            out.append(x)
    if not out:
        raise ValueError("no usable items")
    return out


# --------------------------------------------------------------- fallback
def builtin_words(rng: random.Random, category: str | None = None) -> list:
    pool = [dict(word=w, search_query=q, category=c, difficulty=d)
            for (w, q, c, d) in BUILTIN_WORDS]
    if category:
        sub = [p for p in pool if p["category"] == category.lower()]
        pool = sub or pool
    rng.shuffle(pool)
    return pool


def wordnet_words(rng: random.Random, count: int = 80) -> list:
    """WordNet artifacts filtered by wordfreq. Raises ImportError if the
    libraries or nltk data are missing (caller falls back to BUILTIN_WORDS)."""
    from nltk.corpus import wordnet as wn          # type: ignore
    from wordfreq import zipf_frequency            # type: ignore

    root = wn.synset("artifact.n.01")
    seen, items = set(), []
    for syn in root.closure(lambda s: s.hyponyms()):
        for lem in syn.lemma_names()[:1]:
            w = lem.replace("_", " ").lower()
            if not re.fullmatch(r"[a-z]+( [a-z]+)?", w) or w in seen:
                continue
            f = zipf_frequency(w, "en")
            if 3.6 <= f <= 5.4 and is_valid_shape(w):
                seen.add(w)
                items.append(dict(word=w, search_query=w, category="object", difficulty=2))
    rng.shuffle(items)
    return items[:count]


def fallback_words(rng: random.Random, category: str | None = None) -> tuple:
    """Returns (items, source_name). Never raises."""
    try:
        items = wordnet_words(rng)
        if len(items) >= 12:
            return items, "wordnet"
    except Exception:
        pass
    return builtin_words(rng, category), "builtin"
