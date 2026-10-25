"""$connect: authenticate the WebSocket handshake.

PC:    ?room=<id>&role=pc  + headers  Authorization: Bearer <pc_key>, X-Room-Secret-Hash: <sha256>
       The first PC to present a new room id registers it (trust on first use).
Phone: ?room=<id>&token=<device JWT>      (returning device)
       ?room=<id>&pair=1                  (new device; must then send pair.request)
Returning a non-2xx statusCode rejects the handshake.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from common import auth
from common.models import HEX64_RE, ROOM_ID_RE
from common.presence import broadcast_presence
from common.runtime import get_gateway, get_store

log = logging.getLogger(__name__)

# Per source IP. Generous for a household behind one router (several phones reconnecting at
# once), tight for a script. New rooms are rare: one per install.
CONNECTS_PER_MINUTE = 60
NEW_ROOMS_PER_HOUR = 5


def _reject(status: int, reason: str) -> dict[str, Any]:
    log.info("connect rejected: %s", reason)
    return {"statusCode": status, "body": reason}


def _source_ip(event: dict[str, Any]) -> str | None:
    ip = (event.get("requestContext") or {}).get("identity", {}).get("sourceIp")
    return str(ip) if ip else None


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    connection_id: str = event["requestContext"]["connectionId"]
    query: dict[str, str] = event.get("queryStringParameters") or {}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    room_id = query.get("room", "")
    if not ROOM_ID_RE.match(room_id):
        return _reject(400, "bad room id")

    store = get_store()
    role = query.get("role", "phone")
    ip = _source_ip(event)
    if ip is not None and store.bump_counter(f"ip#{ip}", 60) > CONNECTS_PER_MINUTE:
        return _reject(429, "too many connections")

    if role == "pc":
        bearer = headers.get("authorization", "")
        pc_key = bearer[7:] if bearer.lower().startswith("bearer ") else ""
        secret_hash = headers.get("x-room-secret-hash", "")
        if not pc_key or not HEX64_RE.match(secret_hash):
            return _reject(401, "missing pc credentials")
        pc_key_hash = auth.sha256_hex(pc_key)
        room = store.get_room(room_id)
        new_room = room is None and ip is not None
        if new_room and store.bump_counter(f"newroom#{ip}", 3600) > NEW_ROOMS_PER_HOUR:
            return _reject(429, "too many new rooms")
        if room is None and not store.register_room(room_id, pc_key_hash, secret_hash):
            room = store.get_room(room_id)  # lost the registration race
        elif room is None:
            room = {"pc_key_hash": pc_key_hash, "room_secret_hash": secret_hash}
        assert room is not None
        if not auth.equals(str(room["pc_key_hash"]), pc_key_hash):
            return _reject(401, "bad pc key")
        if room["room_secret_hash"] != secret_hash:
            store.set_room_secret_hash(room_id, secret_hash)  # QR secret was rotated
        store.put_connection(connection_id, room_id, "pc", "pc", authed=True)
        previous = store.swap_pc_connection(room_id, connection_id)
        gateway = get_gateway(event)
        if previous and previous != connection_id:
            gateway.close(previous)  # one PC per room: the newer connection wins
        broadcast_presence(store, gateway, room_id, frozenset({connection_id}))
        return {"statusCode": 200}

    if role != "phone":
        return _reject(400, "bad role")
    room = store.get_room(room_id)
    if room is None:
        return _reject(403, "unknown room")

    token = query.get("token")
    if token:
        key = auth.derive_room_key(store.master_key(), room_id)
        claims = auth.verify_device_token(key, room_id, token, time.time())
        if claims is None:
            return _reject(401, "invalid or expired token")
        device = store.get_device(room_id, claims["sub"])
        if device is None or device.get("revoked"):
            return _reject(401, "device revoked")
        store.put_connection(
            connection_id, room_id, "phone", claims["sub"], authed=True, token_exp=claims["exp"]
        )
        store.touch_device(room_id, claims["sub"])
        broadcast_presence(store, get_gateway(event), room_id, frozenset({connection_id}))
        return {"statusCode": 200}

    if query.get("pair") == "1":
        store.put_connection(connection_id, room_id, "phone", None, authed=False)
        return {"statusCode": 200}
    return _reject(401, "token or pair=1 required")
