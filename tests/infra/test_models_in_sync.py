"""The Lambdas hand-roll envelopes (no pydantic); this keeps them identical to the contract."""

from __future__ import annotations

import json
from typing import get_args

import pytest

import contracts
from common import models
from contracts import Message


def contract_types() -> set[str]:
    members = get_args(get_args(Message)[0])
    return {m.model_fields["type"].default for m in members}


def test_phone_types_match_the_contract() -> None:
    assert models.PHONE_TYPES == contracts.PHONE_TO_SERVER_TYPES


def test_every_type_the_relay_routes_exists_in_the_contract() -> None:
    known = contract_types()
    for group in (
        models.PHONE_TYPES,
        models.COMMAND_TYPES,
        models.PC_BROADCAST_TYPES,
        models.IDEMPOTENT_TYPES,
    ):
        assert group <= known
    assert models.COMMAND_TYPES < models.PHONE_TYPES
    assert models.IDEMPOTENT_TYPES < models.COMMAND_TYPES
    assert {"room.key", "presence", "pair.granted", "pair.denied", "pong"} <= known


def test_protocol_version_and_audio_limit_match() -> None:
    assert models.PROTOCOL_VERSION == contracts.PROTOCOL_VERSION
    chunk = {"command_id": "c", "seq": 0, "b64": "A" * contracts.MAX_AUDIO_CHUNK_B64}
    envelope = contracts.envelope(contracts.AudioChunk, "r", chunk, from_="d")
    assert len(contracts.dump_message(envelope).encode()) < models.MAX_FRAME_BYTES


RELAY_MESSAGES = [
    (
        "presence",
        {
            "devices": [{"device_id": "d", "name": "n", "online": True, "last_seen": 1.0}],
            "pc_online": True,
        },
    ),
    ("pair.granted", {"device_id": "d", "device_token": "t", "expires_at": 1.0}),
    ("pair.denied", {"reason": "invalid_secret"}),
    ("room.key", {"key": "k", "key_id": "i"}),
    ("error", {"code": "c", "message": "m", "retryable": False}),
    ("cmd.ack", {"command_id": "c", "status": "duplicate"}),
    ("pong", {}),
]


@pytest.mark.parametrize(("kind", "payload"), RELAY_MESSAGES)
def test_messages_built_by_the_relay_parse_with_the_contract(
    kind: str, payload: dict[str, object]
) -> None:
    parsed = contracts.parse_message(models.build(kind, "room-abcdef123", payload, "orig"))
    assert parsed.type == kind and parsed.from_ == "relay" and parsed.reply_to == "orig"


def test_contract_messages_pass_the_relay_parser_and_stamping() -> None:
    env = contracts.envelope(
        contracts.CmdButton,
        "room-abcdef123",
        {"command_id": "c", "idempotency_key": "k", "action": "next"},
        from_="dev-1",
    )
    raw = contracts.dump_message(env)
    parsed = models.parse_envelope(raw)
    stamped = json.loads(models.stamp_sender(parsed, "room-abcdef123", "dev-9"))
    assert stamped["from"] == "dev-9"
    assert contracts.parse_message(json.dumps(stamped)).from_ == "dev-9"
