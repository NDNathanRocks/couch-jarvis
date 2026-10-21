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
    """Provider unreachable, rate-limited past retries, or returned an unusable response."""


class TextLLM(Protocol):
    async def complete(self, system: str, user: str) -> LLMReply:
        """Return a reply that should be a single JSON object."""
        ...


class VisionLLM(Protocol):
    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply: ...
