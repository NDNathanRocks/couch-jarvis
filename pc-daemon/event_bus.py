"""In-process pub/sub that fans events out to the cloud relay, LAN WS, SSE streams and the HUD.

Every published message is serialized once, stamped with a monotonically increasing sequence
id and kept in a ring buffer so SSE clients can resume with Last-Event-ID.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from pydantic import BaseModel

from contracts import dump_message


@dataclass(frozen=True)
class BusEvent:
    seq: int
    type: str
    data: str  # envelope JSON

    def to_sse(self) -> str:
        return f"id: {self.seq}\nevent: {self.type}\ndata: {self.data}\n\n"


class EventBus:
    def __init__(self, ring_size: int = 256, subscriber_queue_size: int = 512) -> None:
        self._ring: deque[BusEvent] = deque(maxlen=ring_size)
        self._subs: set[asyncio.Queue[BusEvent]] = set()
        self._lagged: set[asyncio.Queue[BusEvent]] = set()
        self._seq = 0
        self._queue_size = subscriber_queue_size

    @property
    def last_seq(self) -> int:
        return self._seq

    def publish(self, message: BaseModel) -> BusEvent:
        self._seq += 1
        event_type = str(getattr(message, "type", "message"))
        event = BusEvent(seq=self._seq, type=event_type, data=dump_message(message))
        self._ring.append(event)
        for q in tuple(self._subs):
            if q.full():  # slow consumer: drop its oldest event rather than block the daemon
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                self._lagged.add(q)  # ...but remember, so the consumer can resync (see consume_lag)
            q.put_nowait(event)
        return event

    def consume_lag(self, queue: asyncio.Queue[BusEvent]) -> bool:
        """True once after events were dropped for this subscriber.

        A consumer that sees True must resend a fresh state snapshot: it has missed events (for
        example a cmd.result) and would otherwise show stale state forever.
        """
        if queue in self._lagged:
            self._lagged.discard(queue)
            return True
        return False

    def replay_since(self, last_seq: int) -> list[BusEvent]:
        return [e for e in self._ring if e.seq > last_seq]

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[BusEvent]]:
        q: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)
            self._lagged.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
