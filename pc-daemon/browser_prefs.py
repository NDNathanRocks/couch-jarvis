"""Browsing preferences set from the couch (saved on the PC, shown on every phone)."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from contracts import BrowserState

log = logging.getLogger(__name__)


class BrowserPrefs:
    """``browser.json``: a missing or damaged file means the defaults (pop-up protection on).

    The stored field is still called ``adblock`` (the wire contract), but it never blocked ads:
    it closes tabs a site opens by itself and undoes redirects.
    """

    def __init__(
        self, path: Path | None = None, on_change: Callable[[BrowserState], None] | None = None
    ) -> None:
        self._path = path
        self._on_change = on_change or (lambda _state: None)
        self.state = self._load()

    @property
    def adblock(self) -> bool:
        return self.state.adblock

    def apply(self, args: dict[str, Any]) -> str:
        """Change settings; returns the sentence to show."""
        if "adblock" in args:
            self.state = self.state.model_copy(update={"adblock": bool(args["adblock"])})
            self._save()
            self._on_change(self.state)
            return "Pop-up protection on" if self.state.adblock else "Pop-up protection off"
        return "Done"

    def _load(self) -> BrowserState:
        if self._path is not None:
            with contextlib.suppress(OSError, ValueError, ValidationError):
                return BrowserState.model_validate(
                    json.loads(self._path.read_text(encoding="utf-8"))
                )
        return BrowserState()

    def _save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.state.model_dump()), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            log.warning("could not save browser preferences", exc_info=True)
