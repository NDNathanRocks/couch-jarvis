"""Who is connected to a room, as the relay sees it."""

from __future__ import annotations

from typing import Any

from common.apigw import Gateway
from common.ddb import Store
from common.models import build


def presence_message(store: Store, room_id: str) -> str:
    conns = store.list_connections(room_id)
    all_devices = store.list_devices(room_id)
    online = {c.get("device_id") for c in conns if c.get("role") == "phone" and c.get("authed")}
    devices: list[dict[str, Any]] = [
        {
            "device_id": d["device_id"],
            "name": d.get("name", "Phone"),
            "online": d["device_id"] in online,
            "last_seen": float(d.get("last_seen", 0)),
        }
        for d in all_devices
        if not d.get("revoked")
    ]
    pc_online = any(c.get("role") == "pc" for c in conns)
    revoked = sorted(str(d["device_id"]) for d in all_devices if d.get("revoked"))[:100]
    return build(
        "presence", room_id, {"devices": devices, "pc_online": pc_online, "revoked": revoked}
    )


def broadcast_presence(
    store: Store, gateway: Gateway, room_id: str, exclude: frozenset[str] = frozenset()
) -> None:
    """Push presence to the PC and every authenticated phone in the room.

    ``exclude`` is for $connect: the connecting socket is not established yet, so posting to it
    would raise GoneException (and prune its own freshly written record).
    """
    message = presence_message(store, room_id)
    targets = [
        c["connection_id"]
        for c in store.list_connections(room_id)
        if (c.get("role") == "pc" or c.get("authed")) and c["connection_id"] not in exclude
    ]
    gateway.broadcast(targets, message)
