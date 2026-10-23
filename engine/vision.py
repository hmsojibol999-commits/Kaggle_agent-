"""Photo-vs-word check with a Groq vision model.

Why: the word comes from a text model and the photos from a keyword search, so
nothing guaranteed that the picture really shows the word (a parrot cage under
"batting cage", a whole animal under "tail"). This asks a vision model one
narrow question per photo and returns "yes" / "no" / "unsure".

Design rules:
* Never block the pipeline because the vision model is unavailable. Any
  problem (no key, model missing, rate limit, bad JSON) gives "unsure" and the
  caller decides; prepare.py keeps the photo, build.py only fails on a clear "no".
* Only the verdict is stored, never the image, and the key never leaves the
  Authorization header.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time

import requests

from . import errors as E

GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.6-27b"

PROMPT = (
    'A kids/learners vocabulary video asks "What\'s this called?" and the correct '
    'answer is: "{word}".\n'
    "Look at the photo and decide whether a viewer would name its MAIN subject "
    '"{word}".\n'
    "Answer \"yes\" only if ALL are true:\n"
    "1. the main, most prominent object is a {word} (the whole object, not just a "
    "part of something else);\n"
    "2. it is the most natural everyday name for that object;\n"
    "3. no other object in the photo would be an equally good answer.\n"
    'Answer "no" if the photo mainly shows something else, a part of the object, '
    "a person, a drawing/logo, or only text.\n"
    'Answer "unsure" if you cannot tell.\n'
    'Reply ONLY with JSON: {{"verdict":"yes|no|unsure","main_subject":"<2-4 words>"}}'
)


def data_url_from_file(path, max_side: int = 768) -> str:
    """Small JPEG data URL of a local image (no expiring links, ~100 KB)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    k = max_side / max(im.size)
    if k < 1:
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_verdict(text: str) -> dict:
    """Parse the model reply. Anything unreadable is 'unsure'."""
    t = (text or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()      # reasoning models
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    data = None
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return {"verdict": "unsure", "subject": ""}
    v = str(data.get("verdict", "")).strip().lower()
    if v not in ("yes", "no", "unsure"):
        v = "unsure"
    return {"verdict": v, "subject": str(data.get("main_subject", ""))[:60]}


def check_photo(word: str, image_url: str, model: str | None = None,
                key: str | None = None, timeout: int = 60) -> dict:
    """Return {"verdict": yes|no|unsure, "subject": str, "error": code|None}."""
    key = key if key is not None else os.environ.get("GROQ_API_KEY", "")
    model = model or DEFAULT_MODEL
    if not key or not image_url:
        return {"verdict": "unsure", "subject": "", "error": E.GROQ_NO_KEY}
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(word=word)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = E.GROQ_BAD
    for attempt in range(2):
        try:
            r = requests.post(GROQ_CHAT, headers=headers, json=body, timeout=timeout)
        except requests.RequestException:
            last = E.GROQ_BAD
            time.sleep(1 + attempt)
            continue
        if r.status_code == 400 and "response_format" in (r.text or "").lower():
            body.pop("response_format", None)
            continue
        low = (r.text or "").lower()
        if "decommissioned" in low or "model_not_found" in low or "does not exist" in low:
            return {"verdict": "unsure", "subject": "", "error": E.GROQ_MODEL}
        if r.status_code == 429:
            last = E.GROQ_RATE
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (401, 403, 404):
            return {"verdict": "unsure", "subject": "",
                    "error": E.GROQ_NO_KEY if r.status_code == 401 else E.GROQ_MODEL}
        if r.status_code != 200:
            last = E.GROQ_BAD
            time.sleep(1 + attempt)
            continue
        try:
            text = r.json()["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            last = E.GROQ_BAD
            continue
        out = parse_verdict(text)
        out["error"] = None
        return out
    return {"verdict": "unsure", "subject": "", "error": last}


def filter_photos(word: str, photos: list, keep: int, model: str | None = None,
                  key: str | None = None, check=check_photo, pause: float = 0.0) -> tuple:
    """Check photos in order until `keep` have said "yes".

    Returns (kept_photos, report) where report is a dict:
      checked, yes, no, unsure, unavailable (True when the model could not be used).
    Photos are annotated with "vision": verdict. If the model is unavailable the
    photos are returned unchanged (verdict "unsure") so the pipeline keeps working.
    """
    kept, rep = [], {"checked": 0, "yes": 0, "no": 0, "unsure": 0, "unavailable": False}
    fails = 0
    for p in photos:
        if len(kept) >= keep:
            break
        if rep["checked"] and pause:
            time.sleep(pause)
        res = check(word, p.get("large") or p.get("thumb") or "", model, key)
        rep["checked"] += 1
        if res.get("error") in (E.GROQ_NO_KEY, E.GROQ_MODEL):
            rep["unavailable"] = True
            break
        if res.get("error"):
            fails += 1
        v = res["verdict"]
        rep[v] += 1
        if v == "yes":
            kept.append({**p, "vision": "yes"})
        if fails >= 3 and rep["yes"] == 0:        # model keeps failing: stop wasting calls
            rep["unavailable"] = True
            break
    if rep["unavailable"]:
        return [{**p, "vision": "unsure"} for p in photos[:keep]], rep
    return kept, rep
