"""Ollama provider (local text router + qwen2.5vl grounding)."""

from __future__ import annotations

import base64

import httpx

from llm.base import LLMError, LLMReply


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 90.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def _chat(self, system: str, user: str, images: list[str] | None) -> LLMReply:
        message: dict[str, object] = {"role": "user", "content": user}
        if images:
            message["images"] = images
        body = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "keep_alive": "30m",  # keep the model loaded between commands: the first load is slow
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, message],
        }
        try:
            resp = await self._client.post(self._url, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            kind = "model_missing" if exc.response.status_code == 404 else "unavailable"
            raise LLMError(f"ollama request failed: HTTP {exc.response.status_code}", kind) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("ollama request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama request failed: {type(exc).__name__}", "unavailable") from exc
        except ValueError as exc:
            raise LLMError("ollama returned unreadable JSON", "bad_reply") from exc
        return LLMReply(
            text=str(data.get("message", {}).get("content", "")),
            system="ollama",
            model=self._model,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )

    async def complete(self, system: str, user: str) -> LLMReply:
        return await self._chat(system, user, None)

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        return await self._chat(system, user, [base64.b64encode(image_png).decode("ascii")])
