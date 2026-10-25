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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent.graph import AgentRunner
from command_queue import CommandQueue, CommandRequest, Priority, SubmitAck
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
    DeviceRevoke,
    ErrorMsg,
    ErrorPayload,
    InputKey,
    InputPointer,
    InputText,
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
from remote_input import key_combo_allowed, sanitize_typed_text
from stt_engine import STTEngine, STTError
from telemetry import Telemetry
from timers import TimerService
from version import __version__

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_AUDIO_PER_DEVICE = 2
MAX_AUDIO_TOTAL = 8
MAX_CONCURRENT_STT = 2
AUDIO_TTL_S = 30.0
AUDIO_GRACE_S = 4.0  # how long a recording may still be missing parts after audio.end arrived


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


class TokenBucket:
    """Per-key rate limiter: burst immediate actions, refilled at rate per second."""

    def __init__(
        self, rate: float, burst: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate, self._burst, self._clock = rate, burst, clock
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        tokens, last = self._state.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        if tokens < 1:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1, now)
        if len(self._state) > 1024:  # forget idle keys: bounded memory
            self._state = {k: v for k, v in self._state.items() if now - v[1] < 60}
        return True


@dataclass
class _AudioBuffer:
    """One phone's recording. The cloud relay handles every message in its own Lambda call, so
    begin / chunks / end can arrive in any order: the buffer fills from whichever comes first
    and the recording is processed once all three parts are present."""

    device_id: str
    started: float
    idempotency_key: str = ""
    mime: str = ""
    begun: bool = False  # audio.begin seen
    expected: int | None = None  # total_chunks from audio.end, once seen
    duration_ms: int = 0
    deadline: asyncio.TimerHandle | None = None  # fires if parts are still missing after end
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
        idle_poll_s: float = 60.0,
        remote_input: bool = True,
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
        self._idle_poll_s = idle_poll_s
        self._poll_wake = asyncio.Event()
        self.queue = CommandQueue(self._run_command, on_result=self._on_result)
        self._audio: dict[tuple[str, str], _AudioBuffer] = {}  # (device_id, command_id)
        self._limiter = TokenBucket(rate=4.0, burst=12.0)
        self._input_limiter = TokenBucket(rate=60.0, burst=150.0)  # touchpad is chatty by design
        self._slider_limiter = TokenBucket(rate=12.0, burst=30.0)  # a drag sends many values
        self._input_lock = asyncio.Lock()
        self.remote_input_enabled = remote_input
        self.state.update(remote_input=remote_input, daemon_version=__version__)
        self._stt_slots = asyncio.Semaphore(MAX_CONCURRENT_STT)
        self._devices: dict[str, DevicePresence] = {}
        self._cloud_devices: list[DevicePresence] = []
        self.revoked: set[str] = set()
        self.on_revoke: Callable[[str], None] = lambda _device_id: None
        self.on_activity: Callable[[], None] = lambda: None  # any phone message (HUD wake-up)
        self.on_qr_toggle: Callable[[], bool] | None = None  # set when a TV overlay exists
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
        for key in list(self._audio):
            self._drop_audio(key)
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

    def revoke_device(self, device_id: str) -> None:
        """Cut a phone off everywhere: LAN sockets, offline tokens and (via the bus) the relay."""
        self.revoked.add(device_id)
        self.on_revoke(device_id)
        self._devices.pop(device_id, None)
        self._cloud_devices = [d for d in self._cloud_devices if d.device_id != device_id]
        self._push_devices()
        self.bus.publish(envelope(DeviceRevoke, self.room_id, {"device_id": device_id}))

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
        was_watched = self.phones_online
        self.state.update(devices=merged)
        if self.phones_online and not was_watched:
            self._poll_wake.set()  # fresh position/volume for the phone that just arrived

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
        if device_id in self.revoked:
            return [self._error("revoked", "This phone was removed")]
        if msg.type not in PHONE_TO_SERVER_TYPES:
            return [self._error("forbidden_type", f"{msg.type} is not accepted from phones")]
        if msg.type == "ping":
            return [envelope(Pong, self.room_id, {}, reply_to=msg.id)]
        self.on_activity()
        if msg.type == "state.sync":
            return [self.state.snapshot_message()]
        if isinstance(msg, CmdButton):
            button = msg.payload
            if button.action in ("volume_set", "seek_to"):
                return await self._handle_slider(button.action, button.args, device_id)
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
        elif isinstance(msg, InputPointer | InputKey | InputText):
            return await self._handle_input(msg, device_id)
        elif isinstance(msg, AudioBegin):
            return self._audio_begin(msg, device_id)
        elif isinstance(msg, AudioChunk):
            return self._audio_chunk(msg, device_id)
        elif isinstance(msg, AudioEnd):
            return self._audio_end(msg, device_id)
        return []

    async def _handle_slider(
        self, action: str, args: dict[str, str | int | float | bool], device_id: str
    ) -> list[Message]:
        """The volume and timeline sliders: applied at once, not queued behind the agent, with no
        spoken or written reply (a drag would otherwise announce every step)."""
        if not self._slider_limiter.allow(device_id):
            return [self._error("rate_limited", "Slow down", retryable=True)]
        if action == "volume_set":
            level = round(float(args["level"]))
            result = await self._os.set_volume(level)
            if result.ok:
                self.state.update(volume=level, muted=False)
            return []
        result = await self._browser.seek_to(float(args["position_s"]))
        if not result.ok:
            return [self._error("seek_failed", "There is nothing here to seek in")]
        await self.refresh_state()
        return []

    async def _handle_input(
        self, msg: InputPointer | InputKey | InputText, device_id: str
    ) -> list[Message]:
        """Touchpad / keyboard: applied immediately (never queued behind the agent)."""
        if not self.remote_input_enabled:
            return [self._error("input_disabled", "Remote input is turned off on this PC")]
        if not self._input_limiter.allow(device_id):
            return [self._error("rate_limited", "Slow down", retryable=True)]
        os_ctl = self._os
        async with self._input_lock:  # keep one phone's gesture in order, never interleaved
            if isinstance(msg, InputPointer):
                move = msg.payload
                if move.dx or move.dy:
                    await os_ctl.move_rel(move.dx, move.dy)
                if move.scroll:
                    await os_ctl.scroll(move.scroll)
                if move.action:
                    await os_ctl.mouse_action(move.action)
            elif isinstance(msg, InputKey):
                chord = msg.payload
                if not key_combo_allowed(chord.key, chord.mods):
                    name = "+".join([*chord.mods, chord.key])
                    return [self._error("key_not_allowed", f"{name} is blocked")]
                await os_ctl.press_key(chord.key, tuple(chord.mods))
            else:
                text = sanitize_typed_text(msg.payload.text)
                if text:
                    await os_ctl.type_text(text)
        return []

    def submit(self, req: CommandRequest) -> SubmitAck:
        if req.effective_priority() != Priority.EMERGENCY and not self._limiter.allow(
            req.device_id
        ):
            ack = SubmitAck(command_id=req.command_id, status="rejected", reason="rate_limited")
        else:
            ack = self.queue.submit(req)
        self.bus.publish(
            envelope(
                CmdAck,
                self.room_id,
                CmdAckPayload(
                    command_id=req.command_id,
                    status=ack.status,
                    position=ack.position,
                    reason=ack.reason,
                ),
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
        for key in [k for k, b in self._audio.items() if now - b.started > AUDIO_TTL_S]:
            self._drop_audio(key)

    def _drop_audio(self, key: tuple[str, str]) -> _AudioBuffer | None:
        buf = self._audio.pop(key, None)
        if buf is not None and buf.deadline is not None:
            buf.deadline.cancel()
        return buf

    def _audio_slot(self, device_id: str, command_id: str) -> _AudioBuffer:
        """The sender's buffer for this recording, created by whichever part arrives first.

        Buffers are namespaced by sender so one phone can never touch another phone recording,
        and capped so a flood of audio parts cannot exhaust memory (oldest are dropped first).
        """
        key = (device_id, command_id)
        buf = self._audio.get(key)
        if buf is None:
            self._gc_audio()
            mine = [k for k in self._audio if k[0] == device_id]
            while len(mine) >= MAX_AUDIO_PER_DEVICE:
                self._drop_audio(mine.pop(0))
            while len(self._audio) >= MAX_AUDIO_TOTAL:
                self._drop_audio(next(iter(self._audio)))
            buf = self._audio[key] = _AudioBuffer(device_id, time.monotonic())
        return buf

    def _audio_progress(self, key: tuple[str, str]) -> None:
        """Process the recording once begin, every chunk and end are in; else wait (bounded)."""
        buf = self._audio.get(key)
        if buf is None or buf.expected is None:
            return  # audio.end not seen yet: nothing to decide
        if buf.begun and sorted(buf.chunks) == list(range(buf.expected)):
            self._drop_audio(key)
            audio = b"".join(buf.chunks[i] for i in range(buf.expected))
            task = asyncio.create_task(
                self._transcribe_and_submit(key[1], buf, audio, buf.duration_ms)
            )
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        elif buf.deadline is None:  # late parts get a grace period before we give up
            buf.deadline = asyncio.get_running_loop().call_later(
                AUDIO_GRACE_S, self._audio_expired, key
            )

    def _audio_expired(self, key: tuple[str, str]) -> None:
        if self._drop_audio(key) is not None:
            self._publish_result(key[1], False, "Recording was incomplete, try again")

    def _audio_begin(self, msg: AudioBegin, device_id: str) -> list[Message]:
        p = msg.payload
        if self._stt is None:
            return [self._error("stt_unavailable", "Voice is not available on this PC")]
        buf = self._audio_slot(device_id, p.command_id)
        buf.idempotency_key, buf.mime, buf.begun = p.idempotency_key, p.mime, True
        self.bus.publish(
            envelope(
                CmdAck, self.room_id, CmdAckPayload(command_id=p.command_id, status="accepted")
            )
        )
        self._audio_progress((device_id, p.command_id))
        return []

    def _audio_chunk(self, msg: AudioChunk, device_id: str) -> list[Message]:
        p = msg.payload
        key = (device_id, p.command_id)
        try:
            data = base64.b64decode(p.b64, validate=True)
        except (binascii.Error, ValueError):
            self._drop_audio(key)
            return [self._error("bad_audio", "Audio chunk was not valid base64")]
        buf = self._audio_slot(device_id, p.command_id)
        buf.size += len(data)
        if buf.size > MAX_AUDIO_BYTES:
            self._drop_audio(key)
            return [self._error("too_large", "Recording too long")]
        buf.chunks[p.seq] = data
        self._audio_progress(key)
        return []

    def _audio_end(self, msg: AudioEnd, device_id: str) -> list[Message]:
        p = msg.payload
        buf = self._audio_slot(device_id, p.command_id)
        buf.expected, buf.duration_ms = p.total_chunks, p.duration_ms
        self._audio_progress((device_id, p.command_id))
        return []

    async def _transcribe_and_submit(
        self, command_id: str, buf: _AudioBuffer, audio: bytes, duration_ms: int
    ) -> None:
        assert self._stt is not None
        self.progress(command_id, "stt", "start", "")
        try:
            async with self._stt_slots:  # bound GPU/API concurrency however many phones talk
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
        result = await self._runner.run(req)
        if req.kind in ("voice", "text") and req.text:
            result = result.model_copy(update={"heard": req.text[:500]})  # shown in the phone log
        return result

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

    @property
    def phones_online(self) -> bool:
        return any(d.online for d in self.state.state.devices)

    async def _poll_state(self) -> None:
        """Every few seconds while someone watches the phone; rarely when nobody does (the
        relay is paid per message). A phone coming online wakes the poller at once."""
        while True:
            await self.refresh_state()
            self._poll_wake.clear()
            interval = self._poll_s if self.phones_online else self._idle_poll_s
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._poll_wake.wait(), interval)
