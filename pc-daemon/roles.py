"""Owner and guest phones (plan 5H). Every phone is an owner unless the PC's user makes it a guest.

Guests get media, volume, favourites and voice commands; not the touchpad or keyboard, the QR
reset, the PC's voice and browser settings, or deleting saved pages. The PC enforces this (the
relay never needs to know). Stored in %APPDATA%\\CouchJarvis\\roles.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from datafiles import atomic_write_text

Role = Literal["owner", "guest"]


class RoleStore:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._guests: set[str] = set()
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._guests = {d for d in data.get("guests", []) if isinstance(d, str)}
            except (OSError, ValueError, AttributeError):
                self._guests = set()

    def role(self, device_id: str) -> Role:
        return "guest" if device_id in self._guests else "owner"

    def set_role(self, device_id: str, role: Role) -> bool:
        """Returns True when it changed."""
        before = set(self._guests)
        if role == "guest":
            self._guests.add(device_id)
        else:
            self._guests.discard(device_id)
        if self._guests == before:
            return False
        if self._path is not None:
            atomic_write_text(self._path, json.dumps({"guests": sorted(self._guests)}))
        return True

    def forget(self, device_id: str) -> None:
        """A removed phone keeps no role behind."""
        if device_id in self._guests:
            self.set_role(device_id, "owner")
