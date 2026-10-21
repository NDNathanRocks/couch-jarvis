"""Scripted LLM/VLM fakes for tests and --dry-run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from llm.base import LLMError, LLMReply


@dataclass
class ScriptedLLM:
    """Returns queued replies in order. Strings are JSON text; an Exception is raised."""

    replies: list[str | Exception]
    calls: list[tuple[str, str]] = field(default_factory=list)
    image_calls: int = 0

    def _next(self, system: str, user: str) -> LLMReply:
        self.calls.append((system, user))
        if not self.replies:
            raise LLMError("ScriptedLLM exhausted")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMReply(text=item, system="fake", model="fake", input_tokens=10, output_tokens=5)

    async def complete(self, system: str, user: str) -> LLMReply:
        return self._next(system, user)

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        self.image_calls += 1
        return self._next(system, user)


class DryRunRouter:
    """Tiny rule-based stand-in for the router LLM so --dry-run works with no model."""

    async def complete(self, system: str, user: str) -> LLMReply:
        m = re.search(r"Utterance:\s*(.*)", user)
        text = (m.group(1) if m else user).strip().lower()
        play = re.match(
            r"(?:play|put on)\s+(.+?)(?:\s+on\s+(youtube music|youtube|yt music))?$", text
        )
        if play:
            query = play.group(1)
            music = bool(play.group(2)) and "music" in play.group(2)
            plan = {
                "intent": "media.play",
                "confidence": 0.9,
                "slots": {"query": query, "source": "ytmusic" if music else "youtube"},
                "steps": [
                    {
                        "op": "navigate",
                        "args": {"query": query, "source": "ytmusic" if music else "youtube"},
                        "expect": "video_playing",
                    }
                ],
                "source": "llm",
            }
        else:
            plan = {
                "intent": "unknown",
                "confidence": 0.1,
                "slots": {},
                "steps": [],
                "source": "llm",
            }
        return LLMReply(text=json.dumps(plan), system="fake", model="dry-run-router")

    async def complete_with_image(self, system: str, user: str, image_png: bytes) -> LLMReply:
        grounding = {
            "found": True,
            "mark_id": 1,
            "bbox_norm": [0.4, 0.4, 0.6, 0.6],
            "rationale": "dry-run",
        }
        return LLMReply(text=json.dumps(grounding), system="fake", model="dry-run-vlm")
