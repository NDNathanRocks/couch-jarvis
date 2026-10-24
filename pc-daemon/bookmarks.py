"""Saved pages ("Save this page"): a short list kept on the PC and shown on every phone."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from contracts import Bookmark

log = logging.getLogger(__name__)

MAX_BOOKMARKS = 100
_WORDS = re.compile(r"[a-z0-9]+")
_FILLER = {"the", "my", "page", "bookmark", "saved", "one", "a"}


def _tokens(text: str) -> set[str]:
    return set(_WORDS.findall(text.lower()))


class BookmarkStore:
    """``bookmarks.json``. A missing or damaged file means an empty list."""

    def __init__(
        self,
        path: Path | None = None,
        on_change: Callable[[list[Bookmark]], None] | None = None,
    ) -> None:
        self._path = path
        self._on_change = on_change or (lambda _items: None)
        self._items = self._load()

    def items(self) -> list[Bookmark]:
        return [*self._items]

    def add(self, title: str, url: str) -> tuple[Bookmark, bool]:
        """Save a page; returns (bookmark, created). Saving the same URL again just renames it."""
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("only web pages can be saved")
        name = " ".join(title.split())[:120] or urlparse(url).hostname or "Saved page"
        for i, old in enumerate(self._items):
            if old.url == url:
                self._items[i] = old.model_copy(update={"title": name})
                self._changed()
                return self._items[i], False
        item = Bookmark(id=secrets.token_hex(3), title=name, url=url[:2000])
        self._items = [*self._items, item][-MAX_BOOKMARKS:]
        self._changed()
        return item, True

    def remove(self, bookmark_id: str) -> Bookmark | None:
        gone = next((b for b in self._items if b.id == bookmark_id), None)
        if gone is not None:
            self._items = [b for b in self._items if b.id != bookmark_id]
            self._changed()
        return gone

    def find(self, query: str) -> Bookmark | None:
        """An id, or the saved page whose title/site contains all the words said."""
        q = query.strip().lower()
        if not q:
            return None
        for b in self._items:
            if b.id == q:
                return b
        want = _tokens(q) - _FILLER
        if not want:
            return None
        best: tuple[int, Bookmark] | None = None
        for b in self._items:
            have = _tokens(b.title) | _tokens(urlparse(b.url).hostname or "")
            if want <= have and (best is None or len(have) < best[0]):
                best = (len(have), b)  # the tightest match wins
        return best[1] if best else None

    # ------------------------------------------------------------------ storage

    def _changed(self) -> None:
        self._save()
        self._on_change(self.items())

    def _load(self) -> list[Bookmark]:
        if self._path is not None:
            with contextlib.suppress(OSError, ValueError, ValidationError, TypeError):
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return [Bookmark.model_validate(b) for b in raw][-MAX_BOOKMARKS:]
        return []

    def _save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps([b.model_dump() for b in self._items]), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            log.warning("could not save bookmarks", exc_info=True)
