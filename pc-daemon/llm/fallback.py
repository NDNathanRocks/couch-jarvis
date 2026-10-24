"""Try the preferred model first; if it cannot answer, try the next one."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from llm.base import LLMError, LLMReply, TextLLM, VisionLLM

log = logging.getLogger(__name__)


class FallbackLLM:
    """Text and vision calls over ordered providers. Raises the first error if all fail."""

    def __init__(self, providers: Sequence[TextLLM | VisionLLM]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = list(providers)

    async def complete(self, system: str, user: str) -> LLMReply:
        first: LLMError | None = None
        for provider in self._providers:
            try:
                return await provider.complete(system, user)  # type: ignore[union-attr]
            except LLMError as exc:
                log.warning("%s could not answer (%s)", type(provider).__name__, exc)
                first = first or exc
        assert first is not None
        raise first

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        first: LLMError | None = None
        for provider in self._providers:
            try:
                return await provider.complete_with_image(system, user, image_png)  # type: ignore[union-attr]
            except LLMError as exc:
                log.warning("%s could not answer (%s)", type(provider).__name__, exc)
                first = first or exc
        assert first is not None
        raise first
