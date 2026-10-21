"""Offline device authentication for the LAN fallback.

Uses the per-room HS256 key the relay handed us (room.key). Tokens minted by the relay verify
here, and phones can pair directly with the PC over the LAN using the QR room_secret, so the
LAN mode keeps working with the internet down. LAN-minted tokens are only valid on the LAN:
the relay does not know those device ids.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable

import jwt

from contracts import PairGrantedPayload
from room import RoomStore, sha256_hex

ISSUER = "couch-jarvis"
TOKEN_TTL_S = 180 * 24 * 3600


class LanAuth:
    def __init__(self, store: RoomStore, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    @property
    def ready(self) -> bool:
        """False until the PC has connected to the relay at least once and received the key."""
        return self._store.identity.room_key is not None

    def verify_token(self, token: str) -> str | None:
        """Device id for a valid token of this room, else None. Never raises."""
        ident = self._store.identity
        if ident.room_key is None:
            return None
        try:
            claims = jwt.decode(
                token,
                ident.room_key,
                algorithms=["HS256"],
                issuer=ISSUER,
                options={
                    "require": ["exp", "sub", "rid", "iss"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError:
            return None
        sub = claims.get("sub")
        if claims.get("rid") != ident.room_id or not isinstance(sub, str):
            return None
        if float(claims["exp"]) <= self._clock():
            return None
        return sub

    def pair(self, room_secret: str, device_name: str) -> PairGrantedPayload | None:
        """Mint a token if the QR secret is right (constant-time compare). None otherwise."""
        ident = self._store.identity
        if ident.room_key is None or not 0 < len(device_name) <= 64:
            return None
        if not hmac.compare_digest(sha256_hex(room_secret), ident.room_secret_hash):
            return None
        now = self._clock()
        device_id = f"lan-{secrets.token_hex(6)}"
        claims = {
            "iss": ISSUER,
            "sub": device_id,
            "rid": ident.room_id,
            "iat": int(now),
            "exp": int(now + TOKEN_TTL_S),
        }
        token = jwt.encode(claims, ident.room_key, algorithm="HS256")
        return PairGrantedPayload(
            device_id=device_id, device_token=token, expires_at=now + TOKEN_TTL_S
        )
