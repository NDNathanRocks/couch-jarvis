"""Hashing and device-token (HS256 JWT) helpers.

Keys: one random master secret per deployment (never leaves Lambda/DynamoDB). Each room gets a
derived key HMAC(master, "room:" + room_id). The relay signs device tokens with that key and
hands the same key to the PC, so the PC can verify tokens offline (LAN mode) without ever
learning the master secret or other rooms' keys.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import jwt

ISSUER = "couch-jarvis"
TOKEN_TTL_S = 180 * 24 * 3600
REFRESH_WHEN_LEFT_S = 90 * 24 * 3600


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def derive_room_key(master: str, room_id: str) -> str:
    return hmac.new(master.encode(), f"room:{room_id}".encode(), hashlib.sha256).hexdigest()


def key_id(room_key: str) -> str:
    return sha256_hex(room_key)[:12]


def mint_device_token(
    room_key: str, room_id: str, device_id: str, now: float, ttl_s: int = TOKEN_TTL_S
) -> tuple[str, float]:
    exp = now + ttl_s
    claims = {"iss": ISSUER, "sub": device_id, "rid": room_id, "iat": int(now), "exp": int(exp)}
    return jwt.encode(claims, room_key, algorithm="HS256"), exp


def verify_device_token(
    room_key: str, room_id: str, token: str, now: float
) -> dict[str, Any] | None:
    """Return the claims for a valid token for this room, else None (never raises)."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            room_key,
            algorithms=["HS256"],
            issuer=ISSUER,
            options={
                "require": ["exp", "sub", "rid", "iss"],
                # time is checked below against the caller-supplied clock
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.PyJWTError:
        return None
    if claims.get("rid") != room_id or not isinstance(claims.get("sub"), str):
        return None
    if float(claims["exp"]) <= now:
        return None
    return claims
