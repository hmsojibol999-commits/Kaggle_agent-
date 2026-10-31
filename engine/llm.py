"""Which AI provider answers the text / vision requests.

Order: explicit key argument (tests) -> Cloudflare Workers AI (when both
CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID exist) -> Groq (GROQ_API_KEY).
Both speak the OpenAI chat-completions format, so the callers stay the same.
Voices are separate (tts.py: Groq Orpheus -> edge-tts).
"""
from __future__ import annotations

import os

import requests

GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"
CF_CHAT = "https://api.cloudflare.com/client/v4/accounts/{acc}/ai/v1/chat/completions"
CF_TEXT_DEFAULT = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
CF_VISION_DEFAULT = "@cf/meta/llama-3.2-11b-vision-instruct"
# tried in this order when no CF_VISION_MODEL Variable is set; the first one that answers wins
CF_VISION_CANDIDATES = [
    "@cf/meta/llama-3.2-11b-vision-instruct",
    "@cf/mistralai/mistral-small-3.1-24b-instruct",
    "@cf/google/gemma-3-12b-it",
    "@cf/meta/llama-4-scout-17b-16e-instruct",
    "@cf/moonshotai/kimi-k2.6",
]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def groq_enabled() -> bool:
    """Groq is OFF unless the Variable USE_GROQ is 1/true (your Groq organisation is restricted)."""
    return _env("USE_GROQ").lower() in ("1", "true", "yes", "on")


def groq_key() -> str:
    return _env("GROQ_API_KEY") if groq_enabled() else ""


def vision_models() -> list:
    one = _env("CF_VISION_MODEL")
    return [one] if one else list(CF_VISION_CANDIDATES)


def cloudflare_ready() -> bool:
    return bool(_env("CLOUDFLARE_API_TOKEN") and _env("CLOUDFLARE_ACCOUNT_ID"))


def available() -> bool:
    """True when some provider key exists (used to switch vision checks on)."""
    return cloudflare_ready() or bool(groq_key())


def provider_name() -> str:
    return "cloudflare" if cloudflare_ready() else ("groq" if groq_key() else "none")


def resolve(kind: str, groq_model: str, key: str | None = None) -> tuple:
    """Return (url, token, model, provider). kind: 'text' | 'vision'.
    An explicit `key` always means Groq (that is what the unit tests and old callers pass)."""
    if key is None and cloudflare_ready():
        if kind == "vision":
            model = _env("CF_VISION_MODEL") or CF_VISION_DEFAULT
        else:
            model = _env("CF_TEXT_MODEL") or CF_TEXT_DEFAULT
        return (CF_CHAT.format(acc=_env("CLOUDFLARE_ACCOUNT_ID")),
                _env("CLOUDFLARE_API_TOKEN"), model, "cloudflare")
    token = key if key is not None else groq_key()
    return GROQ_CHAT, token, groq_model, "groq"


def accept_model_license(url: str, token: str, model: str) -> bool:
    """Some Cloudflare models (Llama 3.2 vision) need the one-time prompt 'agree'."""
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}",
                                        "Content-Type": "application/json"},
                          json={"model": model, "messages": [{"role": "user", "content": "agree"}],
                                "max_tokens": 5}, timeout=60)
        return r.status_code == 200
    except requests.RequestException:
        return False


def needs_license(text: str) -> bool:
    low = (text or "").lower()
    return "agree" in low and ("agreement" in low or "license" in low or "5016" in low)
