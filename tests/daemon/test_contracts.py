from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    PHONE_TO_SERVER_TYPES,
    AgentProgress,
    AudioBegin,
    AudioChunk,
    AudioEnd,
    CmdAck,
    CmdButton,
    CmdResult,
    CmdText,
    DeviceRevoke,
    ErrorMsg,
    PairDenied,
    PairGranted,
    PairRequest,
    Ping,
    Pong,
    Presence,
    RoomKey,
    StatePatch,
    StateSnapshot,
    StateSync,
    build_schema,
    dump_message,
    new_id,
    parse_message,
)

ROOT = Path(__file__).resolve().parents[2]

COMMON = {"id": "id-1", "ts": 1.0, "room_id": "room", "from": "pc"}

SAMPLES: list[tuple[type, dict[str, object]]] = [
    (PairRequest, {"room_secret": "s", "device_name": "Pixel"}),
    (PairGranted, {"device_id": "d", "device_token": "t", "expires_at": 9.0}),
    (PairDenied, {"reason": "bad secret"}),
    (CmdButton, {"command_id": "c", "idempotency_key": "k", "action": "panic"}),
    (CmdText, {"command_id": "c", "idempotency_key": "k", "text": "pause"}),
    (AudioBegin, {"command_id": "c", "idempotency_key": "k", "mime": "audio/webm"}),
    (AudioChunk, {"command_id": "c", "seq": 0, "b64": "AAAA"}),
    (AudioEnd, {"command_id": "c", "total_chunks": 1, "duration_ms": 900}),
    (CmdAck, {"command_id": "c", "status": "queued", "position": 2}),
    (AgentProgress, {"command_id": "c", "node": "dom_executor", "status": "start"}),
    (CmdResult, {"command_id": "c", "ok": True, "message": "done", "tier_used": "dom"}),
    (StateSnapshot, {"pc_online": True, "volume": 30, "rev": 4}),
    (StatePatch, {"volume": 40, "rev": 5}),
    (StateSync, {}),
    (
        Presence,
        {
            "devices": [{"device_id": "d", "name": "n", "online": True, "last_seen": 1.0}],
            "pc_online": True,
        },
    ),
    (RoomKey, {"key": "abc", "key_id": "k1"}),
    (DeviceRevoke, {"device_id": "dev-1"}),
    (Ping, {}),
    (Pong, {}),
    (ErrorMsg, {"code": "too_large", "message": "x", "retryable": False}),
]


@pytest.mark.parametrize(("cls", "payload"), SAMPLES, ids=[c.__name__ for c, _ in SAMPLES])
def test_round_trip(cls: type, payload: dict[str, object]) -> None:
    msg = cls(**COMMON, payload=payload)
    wire = dump_message(msg)
    assert json.loads(wire)["from"] == "pc"  # alias used on the wire
    again = parse_message(wire)
    assert type(again) is cls
    assert dump_message(again) == wire


def test_every_union_member_is_covered() -> None:
    covered = {cls.model_fields["type"].default for cls, _ in SAMPLES}
    schema_types = {
        ref["properties"]["type"]["const"]
        for ref in build_schema()["$defs"].values()
        if "type" in ref.get("properties", {}) and "const" in ref["properties"]["type"]
    }
    assert covered == schema_types


def test_unknown_type_and_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_message(json.dumps({**COMMON, "v": 1, "type": "nope", "payload": {}}))
    with pytest.raises(ValidationError):
        parse_message(json.dumps({**COMMON, "v": 1, "type": "ping", "payload": {}, "evil": 1}))


def test_audio_chunk_size_limit() -> None:
    with pytest.raises(ValidationError):
        AudioChunk(**COMMON, payload={"command_id": "c", "seq": 0, "b64": "A" * (24 * 1024 + 1)})


def test_unknown_quick_action_rejected() -> None:
    with pytest.raises(ValidationError):
        CmdButton(**COMMON, payload={"command_id": "c", "idempotency_key": "k", "action": "rm_rf"})


def test_phone_cannot_send_server_types() -> None:
    assert "pair.granted" not in PHONE_TO_SERVER_TYPES
    assert "state.patch" not in PHONE_TO_SERVER_TYPES
    assert "cmd.result" not in PHONE_TO_SERVER_TYPES


def test_new_id_is_uuid7_and_time_ordered() -> None:
    a, b = new_id(), new_id()
    assert a != b
    assert a[14] == "7" and a[19] in "89ab"
    import time

    first = new_id()
    time.sleep(0.003)
    assert new_id() > first


def test_committed_schema_has_no_drift() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_contracts.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
