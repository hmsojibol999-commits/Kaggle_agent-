"""Error codes shared with the UI (docs/app.js maps code -> Bengali message)."""
from __future__ import annotations


class WCError(Exception):
    """Failure with a stable code the frontend can translate."""

    def __init__(self, code: str, detail: str = "", stage: str = ""):
        super().__init__(f"[{code}] {detail}")
        self.code = code
        self.detail = detail
        self.stage = stage

    def to_dict(self) -> dict:
        return {"state": "error", "code": self.code, "detail": self.detail[:600],
                "stage": self.stage}


# Codes (keep in sync with ERROR_MESSAGES in docs/app.js)
GROQ_NO_KEY = "groq_no_key"
GROQ_RATE = "groq_rate_limit"
GROQ_MODEL = "groq_model_missing"
GROQ_TERMS = "groq_terms"
GROQ_BAD = "groq_bad_response"
PIXABAY_NO_KEY = "pixabay_no_key"
PIXABAY_RATE = "pixabay_rate_limit"
PIXABAY_EMPTY = "pixabay_empty"
SELECTION_BAD = "selection_bad"
IMAGE_MISSING = "image_missing"
IMAGE_LOW_RES = "image_low_res"
IMAGE_MISMATCH = "image_mismatch"
TTS_FAILED = "tts_failed"
RENDER_FAILED = "render_failed"
GATE_FAILED = "quality_gate_failed"
STATE_FAILED = "state_branch_failed"
RELEASE_FAILED = "release_failed"
UNKNOWN = "unknown"
