"""Command serialization: PriorityQueue + idempotency de-duplication + single worker.

Many friends can press buttons at once; exactly one command runs at a time. Duplicate
idempotency keys never run twice. EMERGENCY commands (panic) cancel an in-flight AGENT run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel

from contracts import CmdResultPayload, QuickAction

log = logging.getLogger(__name__)


class Priority(IntEnum):
    EMERGENCY = 0
    CONTROL = 10
    AGENT = 50
    BACKGROUND = 90


class CommandRequest(BaseModel):
    command_id: str
    idempotency_key: str
    device_id: str
    kind: Literal["button", "text", "voice"]
    text: str | None = None
    action: QuickAction | None = None
    priority: Priority | None = None

    def effective_priority(self) -> Priority:
        if self.priority is not None:
            return self.priority
        if self.kind == "button":
            return Priority.EMERGENCY if self.action == "panic" else Priority.CONTROL
        return Priority.AGENT


class SubmitAck(BaseModel):
    command_id: str
    status: Literal["accepted", "queued", "duplicate", "rejected"]
    position: int | None = None
    reason: Literal["rate_limited", "queue_full"] | None = None
    cached_result: CmdResultPayload | None = None


Handler = Callable[[CommandRequest], Awaitable[CmdResultPayload]]
ResultListener = Callable[[CommandRequest, CmdResultPayload], Awaitable[None] | None]


class CommandQueue:
    def __init__(
        self,
        handler: Handler,
        *,
        idempotency_ttl_s: float = 600.0,
        idempotency_max: int = 2000,
        max_pending: int = 64,
        on_result: ResultListener | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._handler = handler
        self._ttl = idempotency_ttl_s
        self._max = idempotency_max
        self._max_pending = max_pending
        self._on_result = on_result
        self._clock = clock
        self._queue: asyncio.PriorityQueue[tuple[int, int, CommandRequest]] = (
            asyncio.PriorityQueue()
        )
        self._seq = 0
        self._pending: set[tuple[int, int]] = set()
        # key -> (first_seen, result or None while in flight)
        self._seen: OrderedDict[str, tuple[float, CmdResultPayload | None]] = OrderedDict()
        self._current: CommandRequest | None = None
        self._current_task: asyncio.Future[CmdResultPayload] | None = None
        self._worker: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="command-queue-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    # ------------------------------------------------------------------ state

    @property
    def depth(self) -> int:
        return len(self._pending) + (1 if self._current is not None else 0)

    @property
    def driver(self) -> str | None:
        return self._current.device_id if self._current is not None else None

    # ------------------------------------------------------------------ submit

    @staticmethod
    def _scoped(req: CommandRequest) -> str:
        """Keys are per device: one phone can never collide with or pre-empt another phone key."""
        return f"{req.device_id}\x00{req.idempotency_key}"

    def submit(self, req: CommandRequest) -> SubmitAck:
        self._evict_expired()
        known = self._seen.get(self._scoped(req))
        if known is not None:
            return SubmitAck(command_id=req.command_id, status="duplicate", cached_result=known[1])

        prio = req.effective_priority()
        if prio != Priority.EMERGENCY and len(self._pending) >= self._max_pending:
            # Back-pressure: a flood must not grow memory without bound. The key is not recorded,
            # so the phone can retry the same command once the backlog drains.
            return SubmitAck(command_id=req.command_id, status="rejected", reason="queue_full")
        self._seq += 1
        key = (int(prio), self._seq)
        position = sum(1 for p in self._pending if p < key) + (1 if self._current else 0)

        self._seen[self._scoped(req)] = (self._clock(), None)
        self._trim()
        self._pending.add(key)
        self._queue.put_nowait((key[0], key[1], req))

        if (
            prio == Priority.EMERGENCY
            and self._current is not None
            and self._current.effective_priority() >= Priority.AGENT
            and self._current_task is not None
        ):
            self._current_task.cancel()

        return SubmitAck(
            command_id=req.command_id,
            status="queued" if position > 0 else "accepted",
            position=position,
        )

    # ------------------------------------------------------------------ worker

    async def _run(self) -> None:
        while True:
            prio, seq, req = await self._queue.get()
            self._pending.discard((prio, seq))
            self._current = req
            started = self._clock()
            try:
                self._current_task = asyncio.ensure_future(self._handler(req))
                try:
                    result = await self._current_task
                except asyncio.CancelledError:
                    if self._worker_cancelled():
                        raise
                    result = CmdResultPayload(
                        command_id=req.command_id, ok=False, message="cancelled"
                    )
                except Exception as exc:  # handler bugs must never kill the worker
                    log.exception("command handler failed: %s", req.command_id)
                    result = CmdResultPayload(
                        command_id=req.command_id, ok=False, message=f"internal error: {exc}"
                    )
            finally:
                self._current_task = None
                self._current = None
            result = result.model_copy(
                update={"latency_ms": result.latency_ms or (self._clock() - started) * 1000}
            )
            self._seen[self._scoped(req)] = (self._clock(), result)
            await self._notify(req, result)

    def _worker_cancelled(self) -> bool:
        # stop() cancels the worker task itself; handler-task cancellation does not set this.
        task = self._worker
        return task is not None and task.cancelling() > 0

    async def _notify(self, req: CommandRequest, result: CmdResultPayload) -> None:
        if self._on_result is None:
            return
        try:
            maybe = self._on_result(req, result)
            if maybe is not None:
                await maybe
        except Exception:
            log.exception("result listener failed")

    # ------------------------------------------------------------------ idempotency cache

    def _evict_expired(self) -> None:
        now = self._clock()
        while self._seen:
            key, (ts, res) = next(iter(self._seen.items()))
            if res is not None and now - ts > self._ttl:
                self._seen.popitem(last=False)
            else:
                break

    def _trim(self) -> None:
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
