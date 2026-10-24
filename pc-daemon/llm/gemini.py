"""Gemini 2.5 Flash (free tier) provider over the REST API, with 429 backoff."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from llm.base import LLMError, LLMReply

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def _generate(self, system: str, parts: list[dict[str, Any]]) -> LLMReply:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        headers = {"x-goog-api-key": self._api_key}
        url = f"{_BASE}/{self._model}:generateContent"
        last, kind = "no attempts made", "unavailable"
        for attempt in range(self._max_attempts):
            try:
                resp = await self._client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last = f"network error: {type(exc).__name__}"
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "unavailable"
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json())
                last = f"HTTP {resp.status_code}"
                kind = {429: "rate_limited", 401: "bad_key", 403: "bad_key"}.get(
                    resp.status_code, "unavailable"
                )
                if resp.status_code not in (429, 500, 502, 503, 504):
                    break
            await self._sleep(min(1.0 * 2**attempt, 8.0))
        raise LLMError(f"gemini request failed: {last}", kind)

    def _parse(self, data: dict[str, Any]) -> LLMReply:
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("gemini returned no candidate text", "bad_reply") from exc
        usage = data.get("usageMetadata", {})
        return LLMReply(
            text=str(text),
            system="gemini",
            model=self._model,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    async def complete(self, system: str, user: str) -> LLMReply:
        return await self._generate(system, [{"text": user}])

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        image = {"inline_data": {"mime_type": "image/png", "data": _b64(image_png)}}
        return await self._generate(system, [{"text": user}, image])


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
