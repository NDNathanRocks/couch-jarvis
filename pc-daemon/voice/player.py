"""Play a short audio file through the PC's speakers (the TV), and stop it on demand."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import itertools
import logging
import sys
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class Player(Protocol):
    async def play(self, path: Path) -> None: ...

    def stop(self) -> None: ...


class NullPlayer:
    """No speakers (tests, --dry-run): pretends to play."""

    def __init__(self) -> None:
        self.played: list[Path] = []

    async def play(self, path: Path) -> None:
        self.played.append(path)

    def stop(self) -> None:
        return None


class MciPlayer:
    """Windows' built-in media control interface: plays mp3 and wav with no extra packages.

    Every call happens on the event-loop thread (each is quick); playback is polled rather than
    awaited with the blocking ``wait`` flag, so cancelling a reply stops the sound at once.
    """

    _ids = itertools.count(1)

    def __init__(self) -> None:
        self._alias: str | None = None

    def _mci(self, command: str) -> str:
        buf = ctypes.create_unicode_buffer(128)
        code = ctypes.windll.winmm.mciSendStringW(command, buf, 127, 0)
        if code:
            raise OSError(f"MCI error {code} for: {command.split(' ')[0]}")
        return buf.value

    async def play(self, path: Path) -> None:
        if sys.platform != "win32":
            return
        alias = f"cjvoice{next(self._ids)}"
        self._alias = alias
        try:
            self._mci(f'open "{path}" type mpegvideo alias {alias}')
            self._mci(f"play {alias}")
            # MCI has no completion event to await, so poll
            while self._alias == alias and self._mci(f"status {alias} mode") == "playing":  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        except OSError as exc:
            log.warning("could not play the spoken reply: %s", exc)
        finally:
            self._close(alias)

    def _close(self, alias: str) -> None:
        with contextlib.suppress(OSError):
            self._mci(f"stop {alias}")
        with contextlib.suppress(OSError):
            self._mci(f"close {alias}")
        if self._alias == alias:
            self._alias = None

    def stop(self) -> None:
        if self._alias is not None:
            self._close(self._alias)
