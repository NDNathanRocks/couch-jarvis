"""Relay features for many customers: per-IP limits, rooms that expire, deleting your cloud data."""

from __future__ import annotations

import time
from typing import Any

import connect_handler
from common.ddb import ROOM_TTL_S
from infra_support import PC_HEADERS, ROOM, World, msg


def connect_from(world: World, cid: str, ip: str, room: str = ROOM, role: str = "pc") -> int:
    event: dict[str, Any] = World.event(
        cid,
        queryStringParameters={"room": room, "role": role},
        headers=PC_HEADERS if role == "pc" else {},
    )
    event["requestContext"]["identity"] = {"sourceIp": ip}
    return int(connect_handler.handler(event, None)["statusCode"])


def test_one_ip_cannot_open_endless_connections(world: World) -> None:
    codes = [connect_from(world, f"c{i}", "203.0.113.7", role="phone") for i in range(70)]
    assert codes.count(429) == 70 - connect_handler.CONNECTS_PER_MINUTE
    assert codes[: connect_handler.CONNECTS_PER_MINUTE].count(429) == 0
    assert connect_from(world, "other", "198.51.100.1", role="phone") != 429  # others unaffected


def test_one_ip_cannot_register_endless_rooms(world: World) -> None:
    limit = connect_handler.NEW_ROOMS_PER_HOUR
    codes = [connect_from(world, f"pc{i}", "203.0.113.9", room=f"room-{i:04d}") for i in range(8)]
    assert codes[:limit] == [200] * limit
    assert codes[limit:] == [429] * (8 - limit)
    assert world.store.get_room(f"room-{limit:04d}") is None  # refused rooms are not created
    # an existing room reconnecting is not a new room
    assert connect_from(world, "pc-again", "203.0.113.9", room="room-0000") == 200


def test_a_room_expires_unless_its_pc_keeps_connecting(world: World) -> None:
    world.connect_pc()
    room = world.store.get_room(ROOM)
    assert room is not None
    assert abs(int(room["ttl"]) - (time.time() + ROOM_TTL_S)) < 60
    world.store._rooms.update_item(  # noqa: SLF001 - age the record
        Key={"room_id": ROOM},
        UpdateExpression="SET #t = :t",
        ExpressionAttributeNames={"#t": "ttl"},
        ExpressionAttributeValues={":t": 1},
    )
    world.connect_pc("pc-2")  # the PC came back: the expiry moves out again
    assert int(world.store.get_room(ROOM)["ttl"]) > time.time() + ROOM_TTL_S - 60  # type: ignore[index]


def test_deleting_the_room_removes_its_phones_and_disconnects_them(world: World) -> None:
    world.connect_pc()
    ann = world.pair_phone("p1", "Ann")
    world.pair_phone("p2", "Bob")
    world.api.sent.clear()
    world.send("pc-1", msg("room.delete", {}, "del-1"))

    (done,) = world.api.to("pc-1", "room.deleted")
    assert done["reply_to"] == "del-1"
    assert world.store.get_room(ROOM) is None
    assert world.store.list_devices(ROOM) == []
    assert world.store.list_connections(ROOM) == []
    assert {"p1", "p2"} <= set(world.api.closed) and "pc-1" not in world.api.closed
    # a phone that comes back is told the room no longer exists (it asks to pair again)
    assert world.connect_phone("p3", token=ann["device_token"])["statusCode"] == 403


def test_phones_cannot_delete_a_room(world: World) -> None:
    world.connect_pc()
    world.pair_phone("p1", "Ann")
    world.api.sent.clear()
    world.send("p1", msg("room.delete", {}, "del-2"))
    (err,) = world.api.to("p1", "error")
    assert err["payload"]["code"] == "forbidden_type"
    assert world.store.get_room(ROOM) is not None
