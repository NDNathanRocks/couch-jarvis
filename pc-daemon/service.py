"""CommandService: the daemon's brain between transports and the agent.

Transports (cloud relay client, LAN WebSocket, HTTP /api/command) hand it validated envelopes;
it owns the room state, audio reassembly + STT, the command queue and event publication.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent.graph import AgentRunner
from command_queue import CommandQueue, CommandRequest, SubmitAck
from contracts import (
    PHONE_TO_SERVER_TYPES,
    AgentProgress,
    AgentProgressPayload,
    AudioBegin,
    AudioChunk,
    AudioEnd,
    CmdAck,
    CmdAckPayload,
    CmdButton,
    CmdResult,
    CmdResultPayload,
    CmdText,
    DevicePresence,
    ErrorMsg,
    ErrorPayload,
    Message,
    NowPlaying,
    Pong,
    RoomState,
    StatePatch,
    StateSnapshot,
    TimerInfo,
    envelope,
)
from event_bus import EventBus
from executors.base import Browser, OSController
from stt_engine import STTEngine, STTError
from telemetry import Telemetry
from timers import TimerService

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 2 * 1024 * 1024
AUDIO_TTL_S = 30.0


class RoomStateStore:
    """Authoritative RoomState; every change publishes a merge-patch with a bumped rev."""

    def __init__(
        self, bus: EventBus, room_id: str, mode: Literal["cloud", "lan"] = "cloud"
    ) -> None:
        self._bus = bus
        self._room_id = room_id
        self._state = RoomState(mode=mode)

    @property
    def state(self) -> RoomState:
        return self._state

    def snapshot_message(self) -> StateSnapshot:
        return envelope(StateSnapshot, self._room_id, self._state)

    def update(self, **fields: Any) -> bool:
        current = self._state.model_dump()
        changed = {k: v for k, v in fields.items() if current.get(k) != _plain(v)}
        if not changed:
            return False
        rev = self._state.rev + 1
        self._state = RoomState.model_validate(
            {**current, **{k: _plain(v) for k, v in changed.items()}, "rev": rev}
        )
        patch = {k: _plain(v) for k, v in changed.items()} | {"rev": rev}
        self._bus.publish(envelope(StatePatch, self._room_id, patch))
        return True


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


@dataclass
class _AudioBuffer:
    device_id: str
    idempotency_key: str
    mime: str
    started: float
    chunks: dict[int, bytes] = field(default_factory=dict)
    size: int = 0


class CommandService:
    def __init__(
        self,
        *,
        room_id: str,
        bus: EventBus,
        runner: AgentRunner,
        browser: Browser,
        os_ctl: OSController,
        telemetry: Telemetry,
        timers: TimerService,
        stt: STTEngine | None = None,
        mode: Literal["cloud", "lan"] = "cloud",
        state_poll_s: float = 10.0,
    ) -> None:
        self.room_id = room_id
        self.bus = bus
        self.state = RoomStateStore(bus, room_id, mode)
        self.telemetry = telemetry
        self._runner = runner
        runner.set_progress(self.progress)
        self._browser = browser
        self._os = os_ctl
        self._stt = stt
        self._timers = timers
        self._poll_s = state_poll_s
        self.queue = CommandQueue(self._run_command, on_result=self._on_result)
        self._audio: dict[str, _AudioBuffer] = {}
        self._devices: dict[str, DevicePresence] = {}
        self._cloud_devices: list[DevicePresence] = []
        self._bg: set[asyncio.Task[None]] = set()
        self._poller: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.queue.start()
        self.state.update(pc_online=True)
        self._poller = asyncio.create_task(self._poll_state(), name="state-poller")

    async def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
        for task in list(self._bg):
            task.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        await self.queue.stop()
        await self._timers.shutdown()
        self._os.keep_awake(False)
        self.state.update(pc_online=False)

    def progress(self, command_id: str, node: str, status: str, detail: str) -> None:
        self.bus.publish(
            envelope(
                AgentProgress,
                self.room_id,
                AgentProgressPayload(
                    command_id=command_id, node=node, status=status, detail=detail
                ),
            )
        )

    # ------------------------------------------------------------------ devices

    def set_cloud_devices(self, devices: list[DevicePresence]) -> None:
        """Relay presence is authoritative for cloud-paired phones."""
        self._cloud_devices = devices
        self._push_devices()

    def device_connected(self, device_id: str, name: str) -> None:
        self._devices[device_id] = DevicePresence(
            device_id=device_id, name=name, online=True, last_seen=time.time()
        )
        self._push_devices()

    def device_disconnected(self, device_id: str) -> None:
        if device_id in self._devices:
            self._devices[device_id] = self._devices[device_id].model_copy(
                update={"online": False, "last_seen": time.time()}
            )
            self._push_devices()

    def _push_devices(self) -> None:
        lan_ids = {d.device_id for d in self._cloud_devices}
        merged = [
            *self._cloud_devices,
            *(d for d in self._devices.values() if d.device_id not in lan_ids),
        ]
        self.state.update(devices=merged)

    # ------------------------------------------------------------------ inbound

    async def handle_envelope(self, msg: Message, device_id: str) -> list[Message]:
        """Process one phone->PC envelope. Returns replies for the sender only."""
        replies = await self._dispatch(msg, device_id)
        # Errors carry the id they answer so a phone can ignore errors meant for its friends.
        return [
            r.model_copy(update={"reply_to": msg.id})
            if isinstance(r, ErrorMsg) and r.reply_to is None
            else r
            for r in replies
        ]

    async def _dispatch(self, msg: Message, device_id: str) -> list[Message]:
        if msg.type not in PHONE_TO_SERVER_TYPES:
            return [self._error("forbidden_type", f"{msg.type} is not accepted from phones")]
        if msg.type == "ping":
            return [envelope(Pong, self.room_id, {}, reply_to=msg.id)]
        if msg.type == "state.sync":
            return [self.state.snapshot_message()]
        if isinstance(msg, CmdButton):
            button = msg.payload
            self.submit(
                CommandRequest(
                    command_id=button.command_id,
                    idempotency_key=button.idempotency_key,
                    device_id=device_id,
                    kind="button",
                    action=button.action,
                )
            )
        elif isinstance(msg, CmdText):
            typed = msg.payload
            self.submit(
                CommandRequest(
                    command_id=typed.command_id,
                    idempotency_key=typed.idempotency_key,
                    device_id=device_id,
                    kind="text",
                    text=typed.text,
                )
            )
        elif isinstance(msg, AudioBegin):
            return self._audio_begin(msg, device_id)
        elif isinstance(msg, AudioChunk):
            return self._audio_chunk(msg, device_id)
        elif isinstance(msg, AudioEnd):
            return self._audio_end(msg, device_id)
        return []

    def submit(self, req: CommandRequest) -> SubmitAck:
        ack = self.queue.submit(req)
        self.bus.publish(
            envelope(
                CmdAck,
                self.room_id,
                CmdAckPayload(command_id=req.command_id, status=ack.status, position=ack.position),
            )
        )
        if ack.status == "duplicate" and ack.cached_result is not None:
            self.bus.publish(envelope(CmdResult, self.room_id, ack.cached_result))
        self._sync_queue_state()
        return ack

    def _error(
        self, code: str, message: str, retryable: bool = False, reply_to: str | None = None
    ) -> Message:
        return envelope(
            ErrorMsg,
            self.room_id,
            ErrorPayload(code=code, message=message, retryable=retryable),
            reply_to=reply_to,
        )

    # ------------------------------------------------------------------ audio

    def _gc_audio(self) -> None:
        now = time.monotonic()
        for cid in [c for c, b in self._audio.items() if now - b.started > AUDIO_TTL_S]:
            del self._audio[cid]

    def _audio_begin(self, msg: AudioBegin, device_id: str) -> list[Message]:
        self._gc_audio()
        p = msg.payload
        if self._stt is None:
            return [self._error("stt_unavailable", "Voice is not available on this PC")]
        self._audio[p.command_id] = _AudioBuffer(
            device_id, p.idempotency_key, p.mime, time.monotonic()
        )
        self.bus.publish(
            envelope(
                CmdAck, self.room_id, CmdAckPayload(command_id=p.command_id, status="accepted")
            )
        )
        return []

    def _audio_chunk(self, msg: AudioChunk, device_id: str) -> list[Message]:
        p = msg.payload
        buf = self._audio.get(p.command_id)
        if buf is None or buf.device_id != device_id:
            return [self._error("unknown_audio", "No audio stream in progress")]
        try:
            data = base64.b64decode(p.b64, validate=True)
        except (binascii.Error, ValueError):
            self._audio.pop(p.command_id, None)
            return [self._error("bad_audio", "Audio chunk was not valid base64")]
        buf.size += len(data)
        if buf.size > MAX_AUDIO_BYTES:
            self._audio.pop(p.command_id, None)
            return [self._error("too_large", "Recording too long")]
        buf.chunks[p.seq] = data
        return []

    def _audio_end(self, msg: AudioEnd, device_id: str) -> list[Message]:
        p = msg.payload
        buf = self._audio.pop(p.command_id, None)
        if buf is None or buf.device_id != device_id:
            return [self._error("unknown_audio", "No audio stream in progress")]
        if sorted(buf.chunks) != list(range(p.total_chunks)):
            self._publish_result(p.command_id, False, "Recording was incomplete, try again")
            return []
        audio = b"".join(buf.chunks[i] for i in range(p.total_chunks))
        task = asyncio.create_task(
            self._transcribe_and_submit(p.command_id, buf, audio, p.duration_ms)
        )
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return []

    async def _transcribe_and_submit(
        self, command_id: str, buf: _AudioBuffer, audio: bytes, duration_ms: int
    ) -> None:
        assert self._stt is not None
        self.progress(command_id, "stt", "start", "")
        try:
            transcript = await self._stt.transcribe(audio, buf.mime, audio_ms=duration_ms)
        except STTError:
            self._publish_result(command_id, False, "I could not hear that.")
            return
        self.progress(command_id, "stt", "done", transcript.text)
        if not transcript.text.strip():
            self._publish_result(command_id, False, "I did not hear anything.")
            return
        self.submit(
            CommandRequest(
                command_id=command_id,
                idempotency_key=buf.idempotency_key,
                device_id=buf.device_id,
                kind="voice",
                text=transcript.text,
            )
        )

    def _publish_result(self, command_id: str, ok: bool, message: str) -> None:
        self.bus.publish(
            envelope(
                CmdResult,
                self.room_id,
                CmdResultPayload(command_id=command_id, ok=ok, message=message),
            )
        )

    # ------------------------------------------------------------------ queue hooks

    async def _run_command(self, req: CommandRequest) -> CmdResultPayload:
        self._sync_queue_state()
        return await self._runner.run(req)

    async def _on_result(self, req: CommandRequest, result: CmdResultPayload) -> None:
        self.bus.publish(envelope(CmdResult, self.room_id, result))
        await self.refresh_state()
        self._sync_queue_state()

    def _sync_queue_state(self) -> None:
        self.state.update(queue_depth=self.queue.depth, driver=self.queue.driver)

    # ------------------------------------------------------------------ state

    async def refresh_state(self) -> None:
        try:
            snap = await self._browser.snapshot()
            level, muted = await self._os.get_volume()
        except Exception:
            log.warning("state refresh failed", exc_info=True)
            return
        playing = (
            NowPlaying(
                title=snap.title or "Unknown",
                source=snap.source,
                is_playing=bool(snap.is_playing),
                position_s=round(snap.position_s),
                duration_s=round(snap.duration_s),
                thumbnail_url=snap.thumbnail_url,
            )
            if snap.is_playing is not None
            else None
        )
        self._os.keep_awake(bool(snap.is_playing))  # no sleep/screensaver mid-video
        timers: list[TimerInfo] = self._timers.list()
        self.state.update(now_playing=playing, volume=level, muted=muted, timers=timers)

    async def _poll_state(self) -> None:
        while True:
            await self.refresh_state()
            await asyncio.sleep(self._poll_s)
