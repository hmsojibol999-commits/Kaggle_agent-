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

# Parts of other things: the photo would show the whole thing and viewers would
# name that (a lemur photo under "tail"), so these are never valid answers.
PART_WORDS = {
    "tail", "wing", "paw", "claw", "fin", "beak", "horn", "antler", "mane", "trunk",
    "handle", "lid", "cap", "knob", "button", "zipper", "sleeve", "collar", "pocket",
    "wheel", "tire", "pedal", "engine", "bumper", "propeller", "sail", "mast", "deck",
    "leaf", "petal", "branch", "root", "stem", "peel", "shell", "seed", "bone",
    "screen", "lens", "string", "blade", "bristle", "cable", "plug", "switch",
}

BUILTIN_WORDS = [
    # (word, stock-photo search query, category, difficulty 1-3)
    # Curated: interesting / uncommon, but a whole physical thing that photographs well.
    ("quokka", "quokka animal", "animals", 3), ("ocarina", "ocarina instrument", "music", 3),
    ("abacus", "abacus counting", "office", 2), ("pangolin", "pangolin animal", "animals", 3),
    ("axolotl", "axolotl", "animals", 3), ("capybara", "capybara", "animals", 2),
    ("narwhal", "narwhal whale", "animals", 3), ("platypus", "platypus", "animals", 3),
    ("armadillo", "armadillo", "animals", 2), ("aardvark", "aardvark", "animals", 3),
    ("okapi", "okapi", "animals", 3), ("tapir", "tapir animal", "animals", 3),
    ("meerkat", "meerkat", "animals", 2), ("flamingo", "flamingo bird", "animals", 2),
    ("puffin", "puffin bird", "animals", 3), ("toucan", "toucan bird", "animals", 2),
    ("chameleon", "chameleon", "animals", 2), ("seahorse", "seahorse", "animals", 2),
    ("jellyfish", "jellyfish", "animals", 2), ("stingray", "stingray", "animals", 2),
    ("hedgehog", "hedgehog", "animals", 2), ("lemur", "lemur", "animals", 2),
    ("sloth", "sloth animal", "animals", 2), ("walrus", "walrus", "animals", 2),
    ("peacock", "peacock bird", "animals", 2), ("scarab", "scarab beetle", "animals", 3),
    ("mandolin", "mandolin instrument", "music", 3), ("accordion", "accordion instrument", "music", 2),
    ("harmonica", "harmonica", "music", 2), ("tambourine", "tambourine", "music", 2),
    ("xylophone", "xylophone", "music", 2), ("bagpipes", "bagpipes", "music", 3),
    ("didgeridoo", "didgeridoo", "music", 3), ("metronome", "metronome", "music", 3),
    ("banjo", "banjo instrument", "music", 2), ("gramophone", "gramophone", "music", 3),
    ("sextant", "sextant navigation", "tools", 3), ("astrolabe", "astrolabe", "tools", 3),
    ("telescope", "telescope", "science", 2), ("microscope", "microscope laboratory", "science", 2),
    ("stethoscope", "stethoscope", "science", 2), ("periscope", "periscope", "science", 3),
    ("barometer", "barometer", "science", 3), ("kaleidoscope", "kaleidoscope", "toys", 3),
    ("gyroscope", "gyroscope", "science", 3), ("pendulum", "pendulum clock", "science", 3),
    ("sundial", "sundial", "garden", 3), ("weathervane", "weathervane rooster", "garden", 3),
    ("gazebo", "gazebo garden", "garden", 2), ("trellis", "trellis garden", "garden", 3),
    ("scarecrow", "scarecrow field", "garden", 2), ("birdbath", "birdbath garden", "garden", 3),
    ("windmill", "windmill", "outdoors", 2), ("lighthouse", "lighthouse", "outdoors", 2),
    ("igloo", "igloo", "outdoors", 2), ("pagoda", "pagoda temple", "outdoors", 3),
    ("gondola", "gondola venice", "vehicles", 3), ("rickshaw", "rickshaw", "vehicles", 3),
    ("zeppelin", "zeppelin airship", "vehicles", 3), ("sleigh", "sleigh snow", "vehicles", 2),
    ("catamaran", "catamaran boat", "vehicles", 3), ("tuk tuk", "tuk tuk", "vehicles", 3),
    ("unicycle", "unicycle", "sports", 3), ("kayak", "kayak", "sports", 2),
    ("javelin", "javelin sport", "sports", 3), 
    ("fencing mask", "fencing mask", "sports", 3), ("boomerang", "boomerang", "sports", 3),
    ("sombrero", "sombrero hat", "clothing", 2), ("kimono", "kimono", "clothing", 3),
    ("fedora", "fedora hat", "clothing", 3), ("beret", "beret hat", "clothing", 2),
    ("poncho", "poncho", "clothing", 3), ("kilt", "kilt tartan", "clothing", 3),
    ("monocle", "monocle", "clothing", 3), ("cummerbund", "cummerbund", "clothing", 3),
    ("chandelier", "chandelier", "home", 2), ("candelabra", "candelabra", "home", 3),
    ("hourglass", "hourglass sand", "home", 2), ("samovar", "samovar tea", "kitchen", 3),
    ("pestle", "mortar pestle", "kitchen", 3), ("colander", "colander kitchen", "kitchen", 2),
    ("ladle", "ladle spoon kitchen", "kitchen", 2), ("decanter", "decanter", "kitchen", 3),
    ("tagine", "tagine pot", "kitchen", 3), ("wok", "wok pan", "kitchen", 2),
    ("fondue", "fondue pot", "kitchen", 3), ("cruet", "oil vinegar cruet", "kitchen", 3),
    ("typewriter", "typewriter", "office", 2), ("quill", "quill pen ink", "office", 3),
    ("inkwell", "inkwell", "office", 3), ("rolodex", "rolodex", "office", 3),
    ("hole punch", "hole punch", "office", 2),
    ("pomegranate", "pomegranate fruit", "food", 2), ("artichoke", "artichoke", "food", 2),
    ("kumquat", "kumquat", "food", 3), ("persimmon", "persimmon fruit", "food", 3),
    ("dragon fruit", "dragon fruit", "food", 2), ("lychee", "lychee fruit", "food", 3),
    ("starfruit", "starfruit", "food", 3), ("rambutan", "rambutan", "food", 3),
    ("fennel", "fennel bulb", "food", 3), ("kohlrabi", "kohlrabi", "food", 3),
    ("pretzel", "pretzel", "food", 2), ("baguette", "baguette bread", "food", 2),
    ("croissant", "croissant", "food", 2), ("dumpling", "dumplings", "food", 2),
    ("stalactite", "stalactite cave", "science", 3), ("geode", "geode crystal", "science", 3),
    ("fossil", "fossil ammonite", "science", 2), ("compass rose", "compass rose map", "tools", 3),
    ("anvil", "anvil blacksmith", "tools", 2), ("bellows", "bellows fireplace", "tools", 3),
    ("plumb bob", "plumb bob", "tools", 3),
    ("trowel", "trowel garden", "tools", 2), ("awl", "awl leather tool", "tools", 3),
    ("pickaxe", "pickaxe", "tools", 2), ("crowbar", "crowbar", "tools", 2),
    ("hammock", "hammock", "outdoors", 2), ("canteen", "canteen water", "outdoors", 2),
    ("thimble", "thimble sewing", "home", 3), ("spinning wheel", "spinning wheel", "home", 3),
    ("pinwheel", "pinwheel", "toys", 2), 
    ("yo yo", "yo yo toy", "toys", 2), ("rocking horse", "rocking horse", "toys", 2),
    ("matryoshka", "matryoshka doll", "toys", 3), ("pinata", "pinata", "toys", 2),
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
    if len(w.split()) == 1 and singular(w) in PART_WORDS:
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
- interesting, uncommon or thought-provoking words that make a viewer say "what is that?" \
(think quokka, ocarina, abacus, pangolin, sextant, kaleidoscope), NOT everyday words like apple, \
banana, chair, car, dog or cup
- still a concrete, single physical thing (animal, instrument, tool, food, building...) that is \
easy to photograph on its own
- 1 or 2 words, lowercase, singular unless naturally plural (scissors)
- no brands, no people, no animals-in-action, no abstract words
- the WHOLE object only, never a part of something (no tail, wing, handle, lid, wheel, leaf...)
- use the proper name of the thing even when it is rare (\"ocarina\", not \"clay flute\")
- avoid ambiguous words (bat, crane, seal, bark, ring, watch, mouse, date...)
- avoid words whose search could return a different thing (e.g. "batting cage" vs "bird cage")
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
            if 2.6 <= f <= 4.2 and is_valid_shape(w):   # uncommon, but real words
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
