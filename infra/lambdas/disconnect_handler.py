"""$disconnect: forget the connection and tell the room who is still here."""

from __future__ import annotations

import logging
from typing import Any

from common.presence import broadcast_presence
from common.runtime import get_gateway, get_store

log = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    connection_id: str = event["requestContext"]["connectionId"]
    store = get_store()
    old = store.delete_connection(connection_id)
    if old is None:
        return {"statusCode": 200}  # already pruned (e.g. after a GoneException)
    room_id = str(old["room_id"])
    if old.get("role") == "pc":
        store.clear_pc_connection(room_id, connection_id)
    try:
        broadcast_presence(store, get_gateway(event), room_id)
    except Exception:  # presence is best-effort; never fail a disconnect
        log.exception("presence broadcast failed for room %s", room_id)
    return {"statusCode": 200}
