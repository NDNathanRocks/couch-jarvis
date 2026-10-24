"""LLM/VLM provider protocols. Real providers and test fakes both satisfy these."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMReply:
    text: str
    system: str  # gen_ai.system, e.g. "ollama" / "gemini" / "fake"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(RuntimeError):
    """Provider unreachable, rate-limited past retries, or returned an unusable response.

    `kind` says why, so the person on the sofa gets a message they can act on:
    unavailable (not installed / not running), model_missing, timeout, rate_limited,
    bad_key, bad_reply.
    """

    def __init__(self, message: str, kind: str = "unavailable") -> None:
        super().__init__(message)
        self.kind = kind


_BRAIN_MESSAGES = {
    "unavailable": "My brain is not set up on this PC yet. "
    "Run Set up AI brain from the Start menu.",
    "model_missing": "My brain is missing its model. Run Set up AI brain from the Start menu.",
    "timeout": "My brain took too long to answer. Try again, or add a Gemini key for speed.",
    "rate_limited": "My brain is busy right now (rate limit). Try again in a minute.",
    "bad_key": "My Gemini key was rejected. Check CJ_GEMINI_API_KEY in your settings.",
    "bad_reply": "My brain gave an answer I could not read. Try again.",
}


def brain_message(exc: BaseException) -> str:
    """What to tell the user when the language model could not answer."""
    return _BRAIN_MESSAGES.get(getattr(exc, "kind", "unavailable"), _BRAIN_MESSAGES["unavailable"])


class TextLLM(Protocol):
    async def complete(self, system: str, user: str) -> LLMReply:
        """Return a reply that should be a single JSON object."""
        ...


class VisionLLM(Protocol):
    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply: ...
