"""Short-term conversation memory: the last few turns, and a question waiting for an answer."""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.policy import sanitize_untrusted
from agent.state import IntentPlan

MAX_TURNS = 6
IDLE_EXPIRY_S = 30 * 60
PENDING_EXPIRY_S = 60.0


@dataclass
class Turn:
    at: float
    device_id: str
    utterance: str
    ops: list[str]
    args: list[dict[str, str | int | float | bool]]
    ok: bool
    message: str
    page_title: str = ""


@dataclass
class Pending:
    question: str
    choices: list[str]
    asked_at: float
    device_id: str = ""


class ConversationMemory:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self._path, self._clock = path, clock
        self._turns: list[Turn] = []
        self._pending: dict[str, Pending] = {}
        self._load()

    # ------------------------------------------------------------------ turns

    def record(
        self,
        device_id: str,
        utterance: str,
        plan: IntentPlan | None,
        ok: bool,
        message: str,
        page_title: str = "",
    ) -> None:
        steps = plan.steps if plan is not None else []
        self._turns.append(
            Turn(
                at=self._clock(),
                device_id=device_id,
                utterance=utterance[:300],
                ops=[s.op for s in steps],
                args=[dict(s.args) for s in steps],
                ok=ok,
                message=message[:200],
                page_title=page_title[:120],
            )
        )
        del self._turns[:-MAX_TURNS]
        self._save()

    def turns(self) -> list[Turn]:
        self._expire()
        return list(self._turns)

    def render(self) -> str:
        """Text for the router prompt. Everything here came from the user or a page: quoted."""
        turns = self.turns()
        if not turns:
            return ""
        lines = []
        for t in turns:
            steps = ", ".join(
                f"{op}({json.dumps(a, separators=(',', ':'))})" for op, a in zip(t.ops, t.args)
            )
            title = f' page="{sanitize_untrusted(t.page_title, 60)}"' if t.page_title else ""
            lines.append(
                f'- said "{sanitize_untrusted(t.utterance, 120)}" -> [{steps}] '
                f"{'ok' if t.ok else 'FAILED'}{title}"
            )
        return "<recent_actions>\n" + "\n".join(lines) + "\n</recent_actions>"

    def clear(self) -> None:
        self._turns.clear()
        self._pending.clear()
        self._save()

    # ------------------------------------------------------------------ pending question

    def set_pending(self, device_id: str, question: str, choices: list[str]) -> None:
        self._pending[device_id] = Pending(question, choices, self._clock(), device_id)

    def pending(self, device_id: str) -> Pending | None:
        p = self._pending.get(device_id)
        if p is not None and self._clock() - p.asked_at > PENDING_EXPIRY_S:
            del self._pending[device_id]
            return None
        return p

    def clear_pending(self, device_id: str) -> None:
        self._pending.pop(device_id, None)

    # ------------------------------------------------------------------ storage

    def _expire(self) -> None:
        if self._turns and self._clock() - self._turns[-1].at > IDLE_EXPIRY_S:
            self._turns.clear()
            self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"turns": [asdict(t) for t in self._turns]}), encoding="utf-8"
            )

    def _load(self) -> None:
        if self._path is None:
            return
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._turns = [Turn(**t) for t in raw["turns"]][-MAX_TURNS:]
