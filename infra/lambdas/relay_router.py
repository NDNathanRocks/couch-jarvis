"""$default route: pairing, command relay (phone -> PC) and fan-out broadcast (PC -> phones)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from common import auth
from common.apigw import Gateway
from common.ddb import Store
from common.models import (
    COMMAND_TYPES,
    DEVICE_ID_RE,
    IDEMPOTENT_TYPES,
    PC_BROADCAST_TYPES,
    PHONE_TYPES,
    VETTED_FOLLOWUP_TYPES,
    EnvelopeError,
    build,
    parse_envelope,
    stamp_sender,
)
from common.presence import broadcast_presence, presence_message
from common.runtime import get_gateway, get_store

log = logging.getLogger(__name__)

MAX_PAIR_FAILURES = 5
OK = {"statusCode": 200}


def _error(
    gw: Gateway,
    conn_id: str,
    room_id: str,
    code: str,
    message: str,
    retryable: bool = False,
    reply_to: str | None = None,
) -> None:
    payload = {"code": code, "message": message, "retryable": retryable}
    gw.send(conn_id, build("error", room_id, payload, reply_to))


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    conn_id: str = event["requestContext"]["connectionId"]
    store, gw = get_store(), get_gateway(event)
    conn = store.get_connection(conn_id)
    if conn is None:  # expired TTL row or a race with $disconnect
        gw.close(conn_id)
        return OK
    room_id = str(conn["room_id"])
    try:
        msg = parse_envelope(event.get("body") or "")
    except EnvelopeError as exc:
        _error(gw, conn_id, room_id, exc.code, exc.message)
        return OK
    if conn["role"] == "pc":
        _from_pc(store, gw, conn_id, room_id, msg)
    else:
        _from_phone(store, gw, conn, room_id, msg)
    return OK


# ---------------------------------------------------------------------------- PC


def _from_pc(store: Store, gw: Gateway, conn_id: str, room_id: str, msg: dict[str, Any]) -> None:
    kind = msg["type"]
    if kind in PC_BROADCAST_TYPES:
        phones = [
            c["connection_id"]
            for c in store.list_connections(room_id)
            if c.get("role") == "phone" and c.get("authed")
        ]
        gw.broadcast(phones, stamp_sender(msg, room_id, "pc"))
    elif kind == "state.sync":  # the PC's hello after (re)connecting
        key = auth.derive_room_key(store.master_key(), room_id)
        gw.send(
            conn_id,
            build("room.key", room_id, {"key": key, "key_id": auth.key_id(key)}, msg["id"]),
        )
        gw.send(conn_id, presence_message(store, room_id))
    elif kind == "device.revoke":
        _revoke(store, gw, conn_id, room_id, msg)
    elif kind == "ping":
        gw.send(conn_id, build("pong", room_id, {}, msg["id"]))
    elif kind != "pong":
        _error(
            gw,
            conn_id,
            room_id,
            "forbidden_type",
            f"{kind} is not accepted from the PC",
            reply_to=msg["id"],
        )


def _revoke(store: Store, gw: Gateway, conn_id: str, room_id: str, msg: dict[str, Any]) -> None:
    """Owner action: refuse the device from now on and drop its live connections."""
    device_id = msg["payload"].get("device_id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        _error(gw, conn_id, room_id, "bad_message", "device_id required", reply_to=msg["id"])
        return
    if not store.revoke_device(room_id, device_id):
        _error(gw, conn_id, room_id, "unknown_device", "No such device", reply_to=msg["id"])
        return
    for conn in store.list_connections(room_id):
        if conn.get("device_id") == device_id:
            gw.close(str(conn["connection_id"]))
    broadcast_presence(store, gw, room_id)


# -------------------------------------------------------------------------- phone


def _from_phone(
    store: Store, gw: Gateway, conn: dict[str, Any], room_id: str, msg: dict[str, Any]
) -> None:
    conn_id = str(conn["connection_id"])
    kind = msg["type"]
    if kind not in PHONE_TYPES:
        _error(
            gw,
            conn_id,
            room_id,
            "forbidden_type",
            f"{kind} is not accepted from phones",
            reply_to=msg["id"],
        )
        return
    if kind == "ping":
        gw.send(conn_id, build("pong", room_id, {}, msg["id"]))
        return
    if kind == "pong":
        return
    if kind == "pair.request":
        _pair(store, gw, conn, room_id, msg)
        return
    if not conn.get("authed"):
        _error(gw, conn_id, room_id, "unauthorized", "Pair this device first", reply_to=msg["id"])
        return
    device_id = str(conn["device_id"])
    if kind not in VETTED_FOLLOWUP_TYPES:  # audio parts follow an already-vetted audio.begin
        device = store.get_device(room_id, device_id)
        if device is None or device.get("revoked"):
            _error(gw, conn_id, room_id, "revoked", "This phone was removed", reply_to=msg["id"])
            gw.close(conn_id)
            return

    if kind == "state.sync":
        _maybe_refresh_token(store, gw, conn, room_id, device_id)
        gw.send(conn_id, presence_message(store, room_id))
        _forward_to_pc(store, gw, conn_id, room_id, device_id, msg)
        return
    if kind in COMMAND_TYPES:
        _command(store, gw, conn_id, room_id, device_id, msg)


def _pair(
    store: Store, gw: Gateway, conn: dict[str, Any], room_id: str, msg: dict[str, Any]
) -> None:
    conn_id = str(conn["connection_id"])
    payload = msg["payload"]
    secret, name = payload.get("room_secret"), payload.get("device_name")
    room = store.get_room(room_id)
    valid = (
        room is not None
        and isinstance(secret, str)
        and isinstance(name, str)
        and 0 < len(name) <= 64
        and auth.equals(str(room["room_secret_hash"]), auth.sha256_hex(secret))
    )
    if not valid:
        failures = store.record_pair_failure(conn_id)
        gw.send(conn_id, build("pair.denied", room_id, {"reason": "invalid_secret"}, msg["id"]))
        if failures >= MAX_PAIR_FAILURES:
            gw.close(conn_id)
        return
    assert isinstance(name, str)
    device_id = f"dev-{uuid.uuid4().hex[:12]}"
    store.put_device(room_id, device_id, name)
    key = auth.derive_room_key(store.master_key(), room_id)
    token, exp = auth.mint_device_token(key, room_id, device_id, time.time())
    store.authenticate_connection(conn_id, device_id, exp)
    gw.send(
        conn_id,
        build(
            "pair.granted",
            room_id,
            {"device_id": device_id, "device_token": token, "expires_at": exp},
            msg["id"],
        ),
    )
    broadcast_presence(store, gw, room_id)


def _maybe_refresh_token(
    store: Store, gw: Gateway, conn: dict[str, Any], room_id: str, device_id: str
) -> None:
    """Sliding expiry: hand a fresh 180-day token to devices that are active."""
    exp = float(conn.get("token_exp", 0))
    if exp - time.time() >= auth.REFRESH_WHEN_LEFT_S:
        return
    key = auth.derive_room_key(store.master_key(), room_id)
    token, new_exp = auth.mint_device_token(key, room_id, device_id, time.time())
    store.authenticate_connection(str(conn["connection_id"]), device_id, new_exp)
    gw.send(
        str(conn["connection_id"]),
        build(
            "pair.granted",
            room_id,
            {"device_id": device_id, "device_token": token, "expires_at": new_exp},
        ),
    )


def _forward_to_pc(
    store: Store, gw: Gateway, conn_id: str, room_id: str, device_id: str, msg: dict[str, Any]
) -> bool:
    room = store.get_room(room_id) or {}
    pc_conn = room.get("pc_connection_id")
    if pc_conn and gw.send(str(pc_conn), stamp_sender(msg, room_id, device_id)):
        return True
    if pc_conn:
        store.clear_pc_connection(room_id, str(pc_conn))
    _error(gw, conn_id, room_id, "pc_offline", "The PC is not connected", True, msg["id"])
    return False


def _command(
    store: Store, gw: Gateway, conn_id: str, room_id: str, device_id: str, msg: dict[str, Any]
) -> None:
    kind, payload = msg["type"], msg["payload"]
    key = payload.get("idempotency_key")
    if kind in IDEMPOTENT_TYPES:
        if not isinstance(key, str) or not key or len(key) > 128:
            _error(
                gw, conn_id, room_id, "bad_message", "idempotency_key required", reply_to=msg["id"]
            )
            return
        if not store.claim_idempotency(room_id, key):
            gw.send(
                conn_id,
                build(
                    "cmd.ack",
                    room_id,
                    {"command_id": str(payload.get("command_id", "")), "status": "duplicate"},
                    msg["id"],
                ),
            )
            return
    if not _forward_to_pc(store, gw, conn_id, room_id, device_id, msg) and isinstance(key, str):
        store.release_idempotency(room_id, key)  # never delivered: allow the retry
