"""Resilient WebSocket client from the PC daemon to the AWS relay.

Reconnects forever with full-jitter exponential backoff. On every (re)connect it says hello,
receives the room key, and pushes a fresh state snapshot so phones resync instantly.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urlencode

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

from contracts import (
    AudioBegin,
    AudioChunk,
    AudioEnd,
    CmdButton,
    CmdText,
    ErrorMsg,
    Ping,
    Presence,
    RoomKey,
    StateSync,
    dump_message,
    envelope,
    parse_message,
)
from event_bus import EventBus
from room import RoomStore
from service import CommandService
from telemetry import Telemetry

log = logging.getLogger(__name__)

Status = Literal["up", "down", "rejected"]
MAX_FRAME_BYTES = 30 * 1024  # API Gateway frame limit is 32 KB; keep headroom
PHONE_MESSAGES = (CmdButton, CmdText, AudioBegin, AudioChunk, AudioEnd, StateSync)


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
    floor: float = 0.5,
    rng: Callable[[], float] = random.random,
) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2^attempt)), at least floor."""
    ceiling = min(cap, base * 2.0 ** min(attempt, 30))
    return max(floor, rng() * ceiling)


class CloudRelayClient:
    def __init__(
        self,
        *,
        url: str,
        store: RoomStore,
        service: CommandService,
        bus: EventBus,
        telemetry: Telemetry | None = None,
        on_status: Callable[[Status], None] | None = None,
        ping_interval_s: float = 240.0,
        stable_after_s: float = 30.0,
        rng: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._store = store
        self._service = service
        self._bus = bus
        self._telemetry = telemetry or Telemetry(None)
        self._on_status = on_status
        self._ping_interval_s = ping_interval_s
        self._stable_after_s = stable_after_s
        self._rng = rng
        self._sleep = sleep
        self._clock = clock
        self.status: Status = "down"

    def _set_status(self, status: Status) -> None:
        if status != self.status:
            self.status = status
            log.info("cloud relay: %s", status)
            if self._on_status is not None:
                self._on_status(status)

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        attempt = 0
        while True:
            started = self._clock()
            reason = "closed"
            try:
                await self._session()
            except asyncio.CancelledError:
                self._set_status("down")
                raise
            except InvalidStatus as exc:
                code = exc.response.status_code
                reason = f"handshake rejected ({code})"
                if code in (401, 403):
                    self._set_status("rejected")  # wrong pc_key / room owned by another PC
                    attempt = max(attempt, 6)  # no point hammering: go straight to the slow cadence
            except (OSError, WebSocketException, TimeoutError) as exc:
                reason = f"{type(exc).__name__}"
            if self.status != "rejected":
                self._set_status("down")
            if self._clock() - started >= self._stable_after_s:
                attempt = 0
            delay = backoff_delay(attempt, rng=self._rng)
            attempt += 1
            with self._telemetry.span(
                "relay.reconnect",
                attributes={
                    "cj.attempt": attempt,
                    "cj.delay_s": round(delay, 2),
                    "error.type": reason,
                },
            ):
                await self._sleep(delay)

    def _endpoint(self) -> str:
        sep = "&" if "?" in self._url else "?"
        return f"{self._url}{sep}{urlencode({'room': self._store.identity.room_id, 'role': 'pc'})}"

    async def _session(self) -> None:
        ident = self._store.identity
        headers = {
            "Authorization": f"Bearer {ident.pc_key}",
            "X-Room-Secret-Hash": ident.room_secret_hash,
        }
        room_id = ident.room_id
        async with connect(
            self._endpoint(), additional_headers=headers, open_timeout=10, max_size=64 * 1024
        ) as ws:
            self._set_status("up")
            await ws.send(dump_message(envelope(StateSync, room_id, {})))  # hello -> room.key
            await self._send_snapshot(ws)
            tasks = [
                asyncio.create_task(self._receive(ws), name="relay-recv"),
                asyncio.create_task(self._forward_events(ws), name="relay-send"),
                asyncio.create_task(self._heartbeat(ws, room_id), name="relay-ping"),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # re-raise the connection error that ended the session
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ pumps

    async def _send_snapshot(self, ws: ClientConnection) -> None:
        await ws.send(dump_message(self._service.state.snapshot_message()))

    async def _heartbeat(self, ws: ClientConnection, room_id: str) -> None:
        while True:  # API Gateway drops connections idle for 10 minutes
            await asyncio.sleep(self._ping_interval_s)  # real time, independent of backoff
            await ws.send(dump_message(envelope(Ping, room_id, {})))

    async def _forward_events(self, ws: ClientConnection) -> None:
        async with self._bus.subscribe() as queue:
            while True:
                event = await queue.get()
                if len(event.data.encode("utf-8")) > MAX_FRAME_BYTES:
                    log.warning(
                        "dropping oversize %s event (%d bytes)", event.type, len(event.data)
                    )
                    continue
                await ws.send(event.data)

    async def _receive(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                msg = parse_message(raw)
            except ValidationError:
                log.warning("ignoring malformed message from relay")
                continue
            if isinstance(msg, RoomKey):
                self._store.set_room_key(msg.payload.key)
            elif isinstance(msg, Presence):
                self._service.set_cloud_devices(msg.payload.devices)
            elif isinstance(msg, PHONE_MESSAGES):
                replies = await self._service.handle_envelope(msg, msg.from_)
                for reply in replies:
                    await ws.send(dump_message(reply))
            elif isinstance(msg, ErrorMsg):
                log.warning("relay error: %s %s", msg.payload.code, msg.payload.message)
