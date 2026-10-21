"""Dependency-free envelope helpers for the relay Lambdas.

The canonical contract lives in pc-daemon/contracts.py (pydantic). Lambdas deliberately avoid
pydantic (smaller, faster cold starts); tests/infra/test_models_in_sync.py keeps the type sets
below identical to the contract.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 32 * 1024  # API Gateway WebSocket frame limit
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Phone -> relay/PC
PHONE_TYPES = frozenset(
    {
        "pair.request",
        "cmd.button",
        "cmd.text",
        "audio.begin",
        "audio.chunk",
        "audio.end",
        "state.sync",
        "ping",
        "pong",
    }
)
# Commands that are forwarded to the PC (the phone must be authenticated)
COMMAND_TYPES = frozenset({"cmd.button", "cmd.text", "audio.begin", "audio.chunk", "audio.end"})
# Commands carrying an idempotency key that the relay de-duplicates
IDEMPOTENT_TYPES = frozenset({"cmd.button", "cmd.text", "audio.begin"})
# PC -> every phone in the room
PC_BROADCAST_TYPES = frozenset(
    {"state.snapshot", "state.patch", "agent.progress", "cmd.ack", "cmd.result", "error"}
)


class EnvelopeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def new_id() -> str:
    return str(uuid.uuid4())


def parse_envelope(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise EnvelopeError("too_large", "Message exceeds the 32 KB frame limit")
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise EnvelopeError("bad_message", "Message is not valid JSON") from exc
    if not isinstance(msg, dict):
        raise EnvelopeError("bad_message", "Message must be a JSON object")
    if msg.get("v") != PROTOCOL_VERSION:
        raise EnvelopeError("bad_version", "Unsupported protocol version")
    if not isinstance(msg.get("type"), str) or not isinstance(msg.get("id"), str):
        raise EnvelopeError("bad_message", "Message needs string type and id")
    if not isinstance(msg.get("payload"), dict):
        raise EnvelopeError("bad_message", "Message needs an object payload")
    return msg


def build(type_: str, room_id: str, payload: dict[str, Any], reply_to: str | None = None) -> str:
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": type_,
            "id": new_id(),
            "ts": time.time(),
            "room_id": room_id,
            "from": "relay",
            "reply_to": reply_to,
            "payload": payload,
        },
        separators=(",", ":"),
    )


def stamp_sender(msg: dict[str, Any], room_id: str, sender: str) -> str:
    """Overwrite client-supplied identity fields before forwarding."""
    forwarded = {**msg, "room_id": room_id, "from": sender}
    return json.dumps(forwarded, separators=(",", ":"))
