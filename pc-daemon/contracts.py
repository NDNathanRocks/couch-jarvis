"""Canonical wire contracts: WebSocket/SSE envelopes shared by phone, relay and PC.

This module is the single source of truth. scripts/export_contracts.py exports its JSON
Schema to shared/contracts/envelope.schema.json for the PWA TypeScript codegen.
"""

from __future__ import annotations

import os
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1  # keep in sync with _EnvelopeBase.v
MAX_AUDIO_CHUNK_B64 = 24 * 1024

QuickAction = Literal[
    "play_pause",
    "next",
    "prev",
    "vol_up",
    "vol_down",
    "mute",
    "fullscreen",
    "skip_ad",
    "close_tab",
    "seek_fwd_10",
    "seek_back_10",
    "sleep_display",
    "show_qr",
    "panic",
    "back",
    "forward",
    "reload",
    "new_tab",
    "next_tab",
    "prev_tab",
    "scroll_up",
    "scroll_down",
    "subtitles",
]

PointerAction = Literal[
    "click", "right_click", "middle_click", "double_click", "drag_start", "drag_end"
]
Modifier = Literal["ctrl", "alt", "shift"]
# One printable key or a named key. Dangerous ones (F4, Win, ...) are simply not expressible.
REMOTE_KEY_PATTERN = (
    r"^(?:[a-z0-9=\-]|enter|backspace|delete|tab|esc|space|left|right|up|down|home|end"
    r"|pageup|pagedown|f5|f6|f11)$"
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Empty(_Model):
    pass


# ----------------------------------------------------------------------------- room state


class NowPlaying(_Model):
    title: str
    source: Literal["youtube", "ytmusic", "other"] = "other"
    is_playing: bool = False
    position_s: float = 0.0
    duration_s: float = 0.0
    thumbnail_url: str | None = None


class TimerInfo(_Model):
    timer_id: str
    label: str
    ends_at: float
    action: str = "notify"


class DevicePresence(_Model):
    device_id: str
    name: str
    online: bool
    last_seen: float


class RoomState(_Model):
    pc_online: bool = False
    mode: Literal["cloud", "lan"] = "cloud"
    now_playing: NowPlaying | None = None
    volume: int = Field(default=50, ge=0, le=100)
    muted: bool = False
    timers: list[TimerInfo] = Field(default_factory=list)
    queue_depth: int = 0
    driver: str | None = None
    devices: list[DevicePresence] = Field(default_factory=list)
    remote_input: bool = True  # touchpad/keyboard allowed (CJ_REMOTE_INPUT on the PC)
    rev: int = 0


# ----------------------------------------------------------------------------- payloads


class PairRequestPayload(_Model):
    room_secret: str
    device_name: str = Field(max_length=64)


class PairGrantedPayload(_Model):
    device_id: str
    device_token: str
    expires_at: float


class PairDeniedPayload(_Model):
    reason: str


class CmdButtonPayload(_Model):
    command_id: str
    idempotency_key: str
    action: QuickAction
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)


class CmdTextPayload(_Model):
    command_id: str
    idempotency_key: str
    text: str = Field(min_length=1, max_length=500)


class InputPointerPayload(_Model):
    """Relative touchpad movement. scroll is in wheel notches, positive = down."""

    dx: int = Field(default=0, ge=-400, le=400)
    dy: int = Field(default=0, ge=-400, le=400)
    scroll: int = Field(default=0, ge=-30, le=30)
    action: PointerAction | None = None


class InputKeyPayload(_Model):
    key: str = Field(pattern=REMOTE_KEY_PATTERN)
    mods: list[Modifier] = Field(default_factory=list, max_length=3)


class InputTextPayload(_Model):
    text: str = Field(min_length=1, max_length=200)


class AudioBeginPayload(_Model):
    command_id: str
    idempotency_key: str
    mime: str
    sample_rate: int | None = None


class AudioChunkPayload(_Model):
    command_id: str
    seq: int = Field(ge=0)
    b64: str = Field(max_length=MAX_AUDIO_CHUNK_B64)


class AudioEndPayload(_Model):
    command_id: str
    total_chunks: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class CmdAckPayload(_Model):
    command_id: str
    status: Literal["accepted", "queued", "duplicate", "rejected"]
    position: int | None = None
    # why a command was rejected: "rate_limited" (this phone is too fast) or "queue_full"
    reason: Literal["rate_limited", "queue_full"] | None = None


class AgentProgressPayload(_Model):
    command_id: str
    node: str
    status: str
    detail: str = ""


class Suggestion(_Model):
    """A one-tap follow-up offered with a failed result: the phone sends ``text`` as a command."""

    label: str = Field(max_length=60)
    text: str = Field(min_length=1, max_length=500)


class CmdResultPayload(_Model):
    command_id: str
    ok: bool
    message: str
    tier_used: Literal["dom", "js", "vision", "os", "none"] = "none"
    latency_ms: float = 0.0
    heard: str = Field(default="", max_length=500)  # the words we acted on (voice / typed)
    detail: str = Field(default="", max_length=400)  # why it failed, for the phone's log
    suggest: Suggestion | None = None  # "no search bar here: open a new tab and search?"


class PresencePayload(_Model):
    devices: list[DevicePresence]
    pc_online: bool = False
    revoked: list[str] = Field(
        default_factory=list, max_length=100
    )  # device ids, for offline LAN auth


class DeviceRevokePayload(_Model):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class RoomKeyPayload(_Model):
    """Per-room HS256 key the relay hands to the PC so it can verify device tokens offline."""

    key: str
    key_id: str


class ErrorPayload(_Model):
    code: str
    message: str
    retryable: bool = False


# ----------------------------------------------------------------------------- envelopes


class _EnvelopeBase(_Model):
    v: Literal[1] = 1
    id: str
    ts: float
    room_id: str
    from_: str = Field(alias="from")
    reply_to: str | None = None


class PairRequest(_EnvelopeBase):
    type: Literal["pair.request"] = "pair.request"
    payload: PairRequestPayload


class PairGranted(_EnvelopeBase):
    type: Literal["pair.granted"] = "pair.granted"
    payload: PairGrantedPayload


class PairDenied(_EnvelopeBase):
    type: Literal["pair.denied"] = "pair.denied"
    payload: PairDeniedPayload


class CmdButton(_EnvelopeBase):
    type: Literal["cmd.button"] = "cmd.button"
    payload: CmdButtonPayload


class CmdText(_EnvelopeBase):
    type: Literal["cmd.text"] = "cmd.text"
    payload: CmdTextPayload


class InputPointer(_EnvelopeBase):
    type: Literal["input.pointer"] = "input.pointer"
    payload: InputPointerPayload


class InputKey(_EnvelopeBase):
    type: Literal["input.key"] = "input.key"
    payload: InputKeyPayload


class InputText(_EnvelopeBase):
    type: Literal["input.text"] = "input.text"
    payload: InputTextPayload


class AudioBegin(_EnvelopeBase):
    type: Literal["audio.begin"] = "audio.begin"
    payload: AudioBeginPayload


class AudioChunk(_EnvelopeBase):
    type: Literal["audio.chunk"] = "audio.chunk"
    payload: AudioChunkPayload


class AudioEnd(_EnvelopeBase):
    type: Literal["audio.end"] = "audio.end"
    payload: AudioEndPayload


class CmdAck(_EnvelopeBase):
    type: Literal["cmd.ack"] = "cmd.ack"
    payload: CmdAckPayload


class AgentProgress(_EnvelopeBase):
    type: Literal["agent.progress"] = "agent.progress"
    payload: AgentProgressPayload


class CmdResult(_EnvelopeBase):
    type: Literal["cmd.result"] = "cmd.result"
    payload: CmdResultPayload


class StateSnapshot(_EnvelopeBase):
    type: Literal["state.snapshot"] = "state.snapshot"
    payload: RoomState


class StatePatch(_EnvelopeBase):
    """JSON-merge-patch (RFC 7386) of RoomState; rev must be included."""

    type: Literal["state.patch"] = "state.patch"
    payload: dict[str, Any]


class StateSync(_EnvelopeBase):
    type: Literal["state.sync"] = "state.sync"
    payload: Empty = Field(default_factory=Empty)


class Presence(_EnvelopeBase):
    type: Literal["presence"] = "presence"
    payload: PresencePayload


class DeviceRevoke(_EnvelopeBase):
    """PC -> relay: cut a phone off (live connections are closed, future tokens refused)."""

    type: Literal["device.revoke"] = "device.revoke"
    payload: DeviceRevokePayload


class RoomKey(_EnvelopeBase):
    type: Literal["room.key"] = "room.key"
    payload: RoomKeyPayload


class Ping(_EnvelopeBase):
    type: Literal["ping"] = "ping"
    payload: Empty = Field(default_factory=Empty)


class Pong(_EnvelopeBase):
    type: Literal["pong"] = "pong"
    payload: Empty = Field(default_factory=Empty)


class ErrorMsg(_EnvelopeBase):
    type: Literal["error"] = "error"
    payload: ErrorPayload


Message = Annotated[
    PairRequest
    | PairGranted
    | PairDenied
    | CmdButton
    | CmdText
    | InputPointer
    | InputKey
    | InputText
    | AudioBegin
    | AudioChunk
    | AudioEnd
    | CmdAck
    | AgentProgress
    | CmdResult
    | StateSnapshot
    | StatePatch
    | StateSync
    | Presence
    | RoomKey
    | DeviceRevoke
    | Ping
    | Pong
    | ErrorMsg,
    Field(discriminator="type"),
]

message_adapter: TypeAdapter[Message] = TypeAdapter(Message)

# Types a phone may send; the relay and PC must reject anything else from a phone.
PHONE_TO_SERVER_TYPES = frozenset(
    {
        "pair.request",
        "cmd.button",
        "cmd.text",
        "input.pointer",
        "input.key",
        "input.text",
        "audio.begin",
        "audio.chunk",
        "audio.end",
        "state.sync",
        "ping",
        "pong",
    }
)


def new_id() -> str:
    """UUIDv7 (time-ordered) as a canonical string."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 68) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def envelope[E: _EnvelopeBase](
    cls: type[E], room_id: str, payload: Any, *, from_: str = "pc", reply_to: str | None = None
) -> E:
    """Build an outgoing envelope with a fresh id and timestamp."""
    return cls.model_validate(
        {
            "id": new_id(),
            "ts": time.time(),
            "room_id": room_id,
            "from": from_,
            "reply_to": reply_to,
            "payload": payload,
        }
    )


def parse_message(raw: str | bytes) -> Message:
    return message_adapter.validate_json(raw)


def dump_message(msg: BaseModel) -> str:
    return msg.model_dump_json(by_alias=True)


def build_schema() -> dict[str, Any]:
    return message_adapter.json_schema(by_alias=True, mode="validation")
