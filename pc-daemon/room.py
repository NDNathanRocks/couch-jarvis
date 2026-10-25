"""Persistent room identity: room_id, room_secret (in the QR) and pc_key (never leaves the PC).

Secrets are encrypted at rest with Windows DPAPI (current-user scope). On other platforms the
file is written as plain base64 with 0600 permissions, which is only meant for tests/dev.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlencode


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # Both calls share the shape (in, descr, entropy, reserved, prompt, flags, out).
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect_bytes(data: bytes) -> bytes:
    return _dpapi(data, protect=True) if sys.platform == "win32" else data


def unprotect_bytes(data: bytes) -> bytes:
    return _dpapi(data, protect=False) if sys.platform == "win32" else data


@dataclass(frozen=True)
class RoomIdentity:
    room_id: str
    room_secret: str
    pc_key: str
    created_at: float
    # Per-room HS256 key handed to us by the relay; lets us verify device tokens offline (LAN).
    room_key: str | None = None
    revoked: frozenset[str] = frozenset()  # device ids that may no longer connect

    @property
    def room_secret_hash(self) -> str:
        return sha256_hex(self.room_secret)

    @property
    def pc_key_hash(self) -> str:
        return sha256_hex(self.pc_key)

    def pairing_url(self, pwa_base_url: str) -> str:
        query = urlencode({"room": self.room_id, "secret": self.room_secret})
        return f"{pwa_base_url.rstrip('/')}/?{query}"


class RoomStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._identity: RoomIdentity | None = None

    @property
    def identity(self) -> RoomIdentity:
        if self._identity is None:
            return self.load_or_create()
        return self._identity

    def load_or_create(self) -> RoomIdentity:
        if self._path.exists():
            self._identity = self._read()
        else:
            self._identity = RoomIdentity(
                room_id=secrets.token_urlsafe(9),
                room_secret=secrets.token_urlsafe(24),
                pc_key=secrets.token_urlsafe(32),
                created_at=time.time(),
            )
            self._write(self._identity)
        return self._identity

    def start_over(self) -> RoomIdentity:
        """A brand-new room (id, secret, PC key): every phone must pair again. Used after the
        relay has forgotten the old room ("delete my cloud data")."""
        self._identity = None
        self._path.unlink(missing_ok=True)
        return self.load_or_create()

    def rotate_secret(self) -> RoomIdentity:
        """Issue a new room_secret (invalidates the old QR) while keeping room_id and pc_key."""
        current = self.identity
        self._identity = RoomIdentity(
            room_id=current.room_id,
            room_secret=secrets.token_urlsafe(24),
            pc_key=current.pc_key,
            created_at=current.created_at,
            room_key=current.room_key,
            revoked=current.revoked,
        )
        self._write(self._identity)
        return self._identity

    def set_room_key(self, room_key: str) -> RoomIdentity:
        """Remember the relay-issued room key (idempotent)."""
        current = self.identity
        if current.room_key != room_key:
            self._identity = replace(current, room_key=room_key)
            self._write(self._identity)
        return self.identity

    def add_revoked(self, device_ids: Iterable[str]) -> RoomIdentity:
        """Remember revoked device ids (a set that only grows; the relay is authoritative)."""
        current = self.identity
        merged = current.revoked | frozenset(device_ids)
        if merged != current.revoked:
            self._identity = replace(current, revoked=merged)
            self._write(self._identity)
        return self.identity

    def _read(self) -> RoomIdentity:
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        blob = unprotect_bytes(base64.b64decode(doc["secrets"]))
        sec = json.loads(blob)
        return RoomIdentity(
            room_id=doc["room_id"],
            room_secret=sec["room_secret"],
            pc_key=sec["pc_key"],
            created_at=float(doc["created_at"]),
            room_key=sec.get("room_key"),
            revoked=frozenset(doc.get("revoked", [])),
        )

    def _write(self, ident: RoomIdentity) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        sec = json.dumps(
            {"room_secret": ident.room_secret, "pc_key": ident.pc_key, "room_key": ident.room_key}
        ).encode()
        doc = {
            "room_id": ident.room_id,
            "created_at": ident.created_at,
            "revoked": sorted(ident.revoked),
            "secrets": base64.b64encode(protect_bytes(sec)).decode("ascii"),
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        tmp.replace(self._path)
