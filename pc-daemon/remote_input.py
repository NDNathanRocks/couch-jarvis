"""Policy for the touchpad / keyboard a paired phone drives.

A paired phone is trusted far more than model output (it is a person holding a remote), but it
still must not reach the OS shortcuts that escape the media session: the contract only lets a
phone name one printable key or a handful of named keys plus Ctrl/Alt/Shift, and this module
removes the remaining dangerous chords. There is deliberately no Windows key.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from contracts import REMOTE_KEY_PATTERN

MAX_TYPED_CHARS = 200

_KEY = re.compile(REMOTE_KEY_PATTERN)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# (frozenset of modifiers, key) chords that are refused outright.
_DENIED: frozenset[tuple[frozenset[str], str]] = frozenset(
    {
        (frozenset({"ctrl", "shift"}), "esc"),  # task manager
        (frozenset({"ctrl"}), "esc"),  # start menu
        (frozenset({"alt"}), "esc"),
        (frozenset({"ctrl", "shift"}), "delete"),  # clear browsing data
        (frozenset({"ctrl", "shift"}), "backspace"),
    }
)


def key_combo_allowed(key: str, mods: Iterable[str]) -> bool:
    if not _KEY.fullmatch(key):
        return False
    held = frozenset(mods)
    if not held <= {"ctrl", "alt", "shift"}:
        return False
    if {"ctrl", "alt"} <= held:  # Ctrl+Alt+anything: Ctrl+Alt+Del family and AltGr tricks
        return False
    return (held, key) not in _DENIED


def sanitize_typed_text(text: str) -> str:
    """Printable text only (newline allowed), bounded: what a phone keyboard may type."""
    cleaned = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return cleaned[:MAX_TYPED_CHARS]
