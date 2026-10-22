"""Revoking a phone must take effect immediately, not only at its next reconnect (Phase 3)."""

from __future__ import annotations

import time

import pytest

from common import auth
from infra_support import ROOM, World, button, msg


def revoke(world: World, device_id: str, sender: str = "pc-1") -> None:
    world.send(sender, msg("device.revoke", {"device_id": device_id}, "rev-1"))


def setup(world: World) -> tuple[str, str, str]:
    world.connect_pc()
    a = world.pair_phone("p1", "Ann")
    b = world.pair_phone("p2", "Bob")
    world.api.sent.clear()
    return a["device_id"], b["device_id"], a["device_token"]


def test_revoking_drops_the_phones_live_connection_immediately(world: World) -> None:
    ann, _bob, _tok = setup(world)
    revoke(world, ann)
    assert "p1" in world.api.closed and world.store.get_connection("p1") is None
    assert "p2" not in world.api.closed  # other friends are untouched
    assert world.store.get_device(ROOM, ann)["revoked"] is True  # type: ignore[index]


def test_a_revoked_phone_cannot_reconnect_with_its_old_token(world: World) -> None:
    ann, _bob, token = setup(world)
    revoke(world, ann)
    assert world.connect_phone("p9", token=token)["statusCode"] == 401
    assert world.store.get_connection("p9") is None


def test_presence_hides_the_revoked_phone_and_lists_it_for_offline_enforcement(
    world: World,
) -> None:
    ann, bob, _ = setup(world)
    revoke(world, ann)
    for listener in ("pc-1", "p2"):
        (presence,) = world.api.to(listener, "presence")
        payload = presence["payload"]
        assert [d["device_id"] for d in payload["devices"]] == [bob]
        assert payload["revoked"] == [ann]


def test_messages_from_a_still_open_revoked_connection_are_refused(world: World) -> None:
    """Even if a close frame is lost, the next command/sync from that socket goes nowhere."""
    ann, _bob, _ = setup(world)
    world.store.revoke_device(ROOM, ann)  # flag flipped without the close happening
    world.send("p1", button("k-after"))
    (err,) = world.api.to("p1", "error")
    assert err["payload"]["code"] == "revoked"
    assert "p1" in world.api.closed
    assert world.api.to("pc-1", "cmd.button") == []  # never forwarded to the PC


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("input.pointer", {"dx": 5, "dy": 5}),
        ("input.key", {"key": "a", "mods": []}),
        ("input.text", {"text": "hello"}),
    ],
)
def test_keyboard_and_mouse_from_a_revoked_open_connection_never_reach_the_pc(
    world: World, kind: str, payload: dict[str, object]
) -> None:
    """Input is the most privileged message type: it must fail closed even if the close is lost."""
    ann, _bob, _ = setup(world)
    world.store.revoke_device(ROOM, ann)  # flag flipped without the close happening
    world.send("p1", msg(kind, payload))
    (err,) = world.api.to("p1", "error")
    assert err["payload"]["code"] == "revoked"
    assert "p1" in world.api.closed
    assert world.api.to("pc-1", kind) == []


def test_a_revoked_phone_cannot_refresh_its_token(world: World) -> None:
    ann, _bob, _ = setup(world)
    key = world.room_key()
    _old, exp = auth.mint_device_token(key, ROOM, ann, time.time() - 150 * 24 * 3600)
    world.store.authenticate_connection("p1", ann, exp)  # token near expiry: refresh would trigger
    world.store.revoke_device(ROOM, ann)
    world.send("p1", msg("state.sync", {}))
    assert world.api.to("p1", "pair.granted") == []
    assert world.api.to("p1", "error")[0]["payload"]["code"] == "revoked"


def test_revoking_an_unknown_device_creates_no_ghost_record(world: World) -> None:
    setup(world)
    revoke(world, "dev-does-not-exist")
    assert world.api.to("pc-1", "error")[0]["payload"]["code"] == "unknown_device"
    assert world.store.get_device(ROOM, "dev-does-not-exist") is None
    assert len(world.store.list_devices(ROOM)) == 2


def test_revoke_rejects_malformed_device_ids(world: World) -> None:
    setup(world)
    for bad in ("", "x" * 100, "dev/../x", "dev id", 123, None):
        world.send("pc-1", msg("device.revoke", {"device_id": bad}, "bad"))
    codes = [m["payload"]["code"] for m in world.api.to("pc-1", "error")]
    assert codes == ["bad_message"] * 6
    assert all(not d.get("revoked") for d in world.store.list_devices(ROOM))


def test_only_the_pc_can_revoke(world: World) -> None:
    ann, bob, _ = setup(world)
    world.send("p2", msg("device.revoke", {"device_id": ann}, "evil"))  # Bob tries to evict Ann
    assert world.api.to("p2", "error")[0]["payload"]["code"] == "forbidden_type"
    assert not world.store.get_device(ROOM, ann)["revoked"]  # type: ignore[index]
    assert bob and "p1" not in world.api.closed
