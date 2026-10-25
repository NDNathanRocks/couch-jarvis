"""Try the preferred model first; if it cannot answer, try the next one."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from llm.base import LLMError, LLMReply, TextLLM, VisionLLM

log = logging.getLogger(__name__)

# Which failure to tell the user about when every provider failed: the one they can act on.
_ACTIONABLE = ("bad_key", "model_missing", "unavailable", "rate_limited", "timeout", "bad_reply")


def _most_actionable(errors: list[LLMError]) -> LLMError:
    return min(errors, key=lambda e: _ACTIONABLE.index(e.kind) if e.kind in _ACTIONABLE else 99)


class FallbackLLM:
    """Text and vision calls over ordered providers; raises the most useful error if all fail."""

    def __init__(self, providers: Sequence[TextLLM | VisionLLM]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = list(providers)

    async def complete(self, system: str, user: str) -> LLMReply:
        errors: list[LLMError] = []
        for provider in self._providers:
            try:
                return await provider.complete(system, user)  # type: ignore[union-attr]
            except LLMError as exc:
                log.warning("%s could not answer (%s)", type(provider).__name__, exc)
                errors.append(exc)
        raise _most_actionable(errors)

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        errors: list[LLMError] = []
        for provider in self._providers:
            try:
                return await provider.complete_with_image(system, user, image_png)  # type: ignore[union-attr]
            except LLMError as exc:
                log.warning("%s could not answer (%s)", type(provider).__name__, exc)
                errors.append(exc)
        raise _most_actionable(errors)
