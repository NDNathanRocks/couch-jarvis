from __future__ import annotations

import json
import time

from common import auth
from infra_support import ROOM, ROOM_SECRET, World, button, msg


def setup_room(world: World, phones: int = 2) -> list[str]:
    """PC connected + N paired phones (p1..pN). Returns their device ids."""
    world.connect_pc()
    ids = [world.pair_phone(f"p{i}", f"Phone {i}")["device_id"] for i in range(1, phones + 1)]
    world.api.sent.clear()
    return ids


# ------------------------------------------------------------------ pairing


def test_pairing_issues_a_verifiable_token_and_announces_the_friend(world: World) -> None:
    world.connect_pc()
    world.connect_phone("p1", pair="1")
    world.send(
        "p1", msg("pair.request", {"room_secret": ROOM_SECRET, "device_name": "Pixel 9"}, "req-1")
    )
    (granted,) = world.api.to("p1", "pair.granted")
    payload = granted["payload"]
    assert granted["reply_to"] == "req-1" and granted["from"] == "relay"
    claims = auth.verify_device_token(world.room_key(), ROOM, payload["device_token"], time.time())
    assert claims is not None and claims["sub"] == payload["device_id"]
    assert payload["expires_at"] > time.time() + 170 * 24 * 3600
    device = world.store.get_device(ROOM, payload["device_id"])
    assert device is not None and device["name"] == "Pixel 9" and device["revoked"] is False
    assert world.store.get_connection("p1")["authed"] is True  # type: ignore[index]
    (presence,) = world.api.to("pc-1", "presence")
    assert [d["name"] for d in presence["payload"]["devices"]] == ["Pixel 9"]


def test_wrong_secret_is_denied_and_five_failures_drop_the_socket(world: World) -> None:
    world.connect_pc()
    world.connect_phone("p1", pair="1")
    for attempt in range(5):
        world.send(
            "p1", msg("pair.request", {"room_secret": "guess", "device_name": "x"}, f"r{attempt}")
        )
    assert len(world.api.to("p1", "pair.denied")) == 5
    assert world.api.to("p1", "pair.granted") == []
    assert "p1" in world.api.closed and world.store.get_connection("p1") is None
    assert world.store.list_devices(ROOM) == []


def test_invalid_pair_payloads_are_denied(world: World) -> None:
    world.connect_pc()
    world.connect_phone("p1", pair="1")
    world.send("p1", msg("pair.request", {"room_secret": ROOM_SECRET, "device_name": "n" * 65}))
    world.send("p1", msg("pair.request", {"room_secret": ROOM_SECRET}))
    world.send("p1", msg("pair.request", {"room_secret": 123, "device_name": "x"}))
    assert len(world.api.to("p1", "pair.denied")) == 3 and world.api.to("p1", "pair.granted") == []


def test_unpaired_phone_cannot_send_commands(world: World) -> None:
    world.connect_pc()
    world.connect_phone("p1", pair="1")
    world.api.sent.clear()
    world.send("p1", button())
    (err,) = world.api.to("p1", "error")
    assert err["payload"]["code"] == "unauthorized"
    assert world.api.to("pc-1") == []


# ------------------------------------------------------------------ phone -> PC


def test_commands_go_only_to_the_pc_with_a_trusted_sender(world: World) -> None:
    ids = setup_room(world)
    spoof = button()
    spoof["from"] = "pc"  # a phone pretending to be the PC
    spoof["room_id"] = "another-room-xyz"
    world.send("p1", spoof)
    (delivered,) = world.api.to("pc-1", "cmd.button")
    assert delivered["from"] == ids[0] and delivered["room_id"] == ROOM
    assert world.api.to("p2") == []  # other phones never see raw commands
    assert world.api.to("p1", "error") == []


def test_every_command_type_is_relayed(world: World) -> None:
    setup_room(world, 1)
    world.send("p1", msg("cmd.text", {"command_id": "c", "idempotency_key": "t", "text": "pause"}))
    world.send(
        "p1", msg("audio.begin", {"command_id": "a", "idempotency_key": "ab", "mime": "audio/webm"})
    )
    world.send("p1", msg("audio.chunk", {"command_id": "a", "seq": 0, "b64": "AAAA"}))
    world.send("p1", msg("audio.end", {"command_id": "a", "total_chunks": 1, "duration_ms": 5}))
    assert [m["type"] for m in world.api.to("pc-1")] == [
        "cmd.text",
        "audio.begin",
        "audio.chunk",
        "audio.end",
    ]


def test_duplicate_idempotency_key_is_answered_by_the_relay(world: World) -> None:
    setup_room(world, 2)
    world.send("p1", button("same", "c1"))
    world.send("p2", button("same", "c2"))  # a friend double-taps
    assert len(world.api.to("pc-1", "cmd.button")) == 1
    (ack,) = world.api.to("p2", "cmd.ack")
    assert ack["payload"] == {"command_id": "c2", "status": "duplicate"}


def test_idempotency_keys_are_scoped_per_room(world: World) -> None:
    assert world.store.claim_idempotency("room-one-1234", "k") is True
    assert world.store.claim_idempotency("room-one-1234", "k") is False
    assert world.store.claim_idempotency("room-two-1234", "k") is True


def test_command_without_idempotency_key_is_rejected(world: World) -> None:
    setup_room(world, 1)
    world.send("p1", msg("cmd.button", {"command_id": "c", "action": "next"}))
    assert world.api.to("p1", "error")[0]["payload"]["code"] == "bad_message"
    assert world.api.to("pc-1") == []


def test_pc_offline_returns_retryable_error_and_frees_the_key(world: World) -> None:
    world.connect_pc()
    world.pair_phone("p1")
    world.disconnect("pc-1")
    world.api.sent.clear()
    world.send("p1", button("k-offline"))
    (err,) = world.api.to("p1", "error")
    assert err["payload"] == {
        "code": "pc_offline",
        "message": "The PC is not connected",
        "retryable": True,
    }
    # the PC comes back; the same retry must not be treated as a duplicate
    world.connect_pc("pc-2")
    world.send("p1", button("k-offline"))
    assert len(world.api.to("pc-2", "cmd.button")) == 1


def test_stale_pc_connection_is_pruned_on_gone(world: World) -> None:
    setup_room(world, 1)
    world.api.gone.add("pc-1")  # the PC vanished without a $disconnect
    world.send("p1", button("k1"))
    assert world.api.to("p1", "error")[0]["payload"]["code"] == "pc_offline"
    assert world.store.get_connection("pc-1") is None
    assert "pc_connection_id" not in world.store.get_room(ROOM)  # type: ignore[operator]


# ------------------------------------------------------------------ PC -> phones


def test_pc_events_fan_out_to_authenticated_phones_only(world: World) -> None:
    setup_room(world, 2)
    world.connect_phone("p3", pair="1")  # connected but not paired
    world.api.sent.clear()
    for kind, payload in [
        ("state.snapshot", {"rev": 1}),
        ("state.patch", {"volume": 5, "rev": 2}),
        ("agent.progress", {"command_id": "c", "node": "n", "status": "s"}),
        ("cmd.ack", {"command_id": "c", "status": "accepted"}),
        ("cmd.result", {"command_id": "c", "ok": True, "message": "m"}),
    ]:
        world.send("pc-1", msg(kind, payload, f"id-{kind}"))
    for phone in ("p1", "p2"):
        assert [m["type"] for m in world.api.to(phone)] == [
            "state.snapshot",
            "state.patch",
            "agent.progress",
            "cmd.ack",
            "cmd.result",
        ]
    assert world.api.to("p3") == [] and world.api.to("pc-1") == []
    assert world.api.to("p1")[0]["from"] == "pc"


def test_gone_phone_is_pruned_during_fan_out(world: World) -> None:
    setup_room(world, 2)
    world.api.gone.add("p2")
    world.send("pc-1", msg("state.patch", {"volume": 1, "rev": 3}))
    assert len(world.api.to("p1", "state.patch")) == 1
    assert world.store.get_connection("p2") is None and world.store.get_connection("p1") is not None


def test_phone_cannot_send_server_only_types(world: World) -> None:
    setup_room(world, 1)
    for kind in (
        "pair.granted",
        "state.patch",
        "state.snapshot",
        "cmd.result",
        "presence",
        "room.key",
    ):
        world.send("p1", msg(kind, {}))
    codes = [m["payload"]["code"] for m in world.api.to("p1", "error")]
    assert codes == ["forbidden_type"] * 6
    assert world.api.to("pc-1") == []


def test_pc_hello_receives_the_room_key_and_presence(world: World) -> None:
    world.connect_pc()
    world.send("pc-1", msg("state.sync", {}, "hello"))
    (key_msg,) = world.api.to("pc-1", "room.key")
    assert key_msg["payload"]["key"] == world.room_key()
    assert key_msg["payload"]["key_id"] == auth.key_id(world.room_key())
    assert key_msg["reply_to"] == "hello"
    assert world.api.to("pc-1", "presence")[0]["payload"]["pc_online"] is True


def test_room_keys_never_cross_rooms(world: World) -> None:
    other = "room-other-4321"
    from infra_support import PC_HEADERS

    world.connect_pc("pc-a")
    world.connect_pc("pc-b", room=other)
    world.send("pc-a", msg("state.sync", {}))
    world.send("pc-b", msg("state.sync", {}))
    key_a = world.api.to("pc-a", "room.key")[0]["payload"]["key"]
    key_b = world.api.to("pc-b", "room.key")[0]["payload"]["key"]
    assert key_a != key_b and PC_HEADERS


def test_phone_state_sync_is_forwarded_and_answered_with_presence(world: World) -> None:
    ids = setup_room(world, 1)
    world.send("p1", msg("state.sync", {}, "sync-1"))
    (fwd,) = world.api.to("pc-1", "state.sync")
    assert fwd["from"] == ids[0]
    assert world.api.to("p1", "presence")


def test_active_devices_get_a_fresh_token_before_expiry(world: World) -> None:
    ids = setup_room(world, 1)
    key = world.room_key()
    # simulate a connection whose token has 30 days left
    old, exp = auth.mint_device_token(key, ROOM, ids[0], time.time() - 150 * 24 * 3600)
    world.store.authenticate_connection("p1", ids[0], exp)
    world.send("p1", msg("state.sync", {}))
    (refresh,) = world.api.to("p1", "pair.granted")
    new_exp = refresh["payload"]["expires_at"]
    assert new_exp > time.time() + 170 * 24 * 3600 and refresh["payload"]["device_token"] != old
    world.api.sent.clear()
    world.send("p1", msg("state.sync", {}))  # a fresh token is not re-issued every sync
    assert world.api.to("p1", "pair.granted") == []


# ------------------------------------------------------------------ framing


def test_oversize_and_malformed_messages_get_errors(world: World) -> None:
    setup_room(world, 1)
    big = json.dumps(
        msg("cmd.text", {"command_id": "c", "idempotency_key": "k", "text": "x" * 40000})
    )
    world.send("p1", big)
    world.send("p1", "{not json")
    world.send("p1", "[1,2,3]")
    world.send("p1", json.dumps({**msg("ping", {}), "v": 2}))
    world.send("p1", json.dumps({"v": 1, "type": "ping", "id": "x", "payload": "no"}))
    codes = [m["payload"]["code"] for m in world.api.to("p1", "error")]
    assert codes == ["too_large", "bad_message", "bad_message", "bad_version", "bad_message"]
    assert world.api.to("pc-1") == []


def test_ping_pong(world: World) -> None:
    setup_room(world, 1)
    world.send("p1", msg("ping", {}, "pp1"))
    world.send("pc-1", msg("ping", {}, "pp2"))
    assert world.api.to("p1", "pong")[0]["reply_to"] == "pp1"
    assert world.api.to("pc-1", "pong")[0]["reply_to"] == "pp2"


def test_messages_from_unknown_connections_close_them(world: World) -> None:
    world.send("ghost", msg("ping", {}))
    assert "ghost" in world.api.closed
