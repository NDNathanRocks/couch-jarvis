from __future__ import annotations

from infra_support import ROOM, World


def test_phone_disconnect_marks_it_offline_for_everyone_else(world: World) -> None:
    world.connect_pc()
    a = world.pair_phone("p1", "Ann")["device_id"]
    world.pair_phone("p2", "Bob")
    world.api.sent.clear()
    assert world.disconnect("p1")["statusCode"] == 200
    assert world.store.get_connection("p1") is None
    for listener in ("pc-1", "p2"):
        (presence,) = world.api.to(listener, "presence")
        by_id = {d["device_id"]: d for d in presence["payload"]["devices"]}
        assert by_id[a]["online"] is False and by_id[a]["name"] == "Ann"
    assert world.api.to("p1") == []  # nothing is sent to the socket that just left


def test_pc_disconnect_clears_room_and_tells_phones(world: World) -> None:
    world.connect_pc()
    world.pair_phone("p1")
    world.api.sent.clear()
    world.disconnect("pc-1")
    assert "pc_connection_id" not in world.store.get_room(ROOM)  # type: ignore[operator]
    (presence,) = world.api.to("p1", "presence")
    assert presence["payload"]["pc_online"] is False


def test_replaced_pc_connection_disconnecting_does_not_clear_the_new_one(world: World) -> None:
    world.connect_pc("pc-1")
    world.connect_pc("pc-2")
    world.disconnect("pc-1")  # the old socket's late $disconnect
    assert world.store.get_room(ROOM)["pc_connection_id"] == "pc-2"  # type: ignore[index]


def test_disconnect_of_unknown_connection_is_harmless(world: World) -> None:
    assert world.disconnect("never-seen")["statusCode"] == 200


def test_revoked_devices_vanish_from_presence(world: World) -> None:
    world.connect_pc()
    gone_id = world.pair_phone("p1", "Ex-friend")["device_id"]
    world.pair_phone("p2", "Friend")
    world.store.revoke_device(ROOM, gone_id)
    world.api.sent.clear()
    world.disconnect("p2")
    (presence,) = world.api.to("pc-1", "presence")
    assert [d["name"] for d in presence["payload"]["devices"]] == ["Friend"]
