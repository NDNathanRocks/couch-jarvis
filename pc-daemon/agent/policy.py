"""Trust policy for anything an LLM (or a web page that influences it) can ask the PC to do.

The router LLM sees text from web pages (the tab title), so its output must be treated as
attacker-influenced. Prompts alone are not a security boundary; these checks are enforced in
code, at both the agent and the executor layer.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Single keys and chords that are useful for media control and harmless to send. Notably absent:
# the Windows key, Alt+F4, Ctrl+Alt chords, Enter/Tab/Delete, function keys that launch things,
# and every multi-character sequence.
ALLOWED_KEY_COMBOS: frozenset[str] = frozenset(
    {
        "space", "esc", "f", "f11", "m", "k", "j", "l", "c",
        "left", "right", "up", "down",
        "shift+n", "shift+p", "shift+left", "shift+right",
    }
)  # fmt: skip

MEDIA_HOSTS: frozenset[str] = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)


def normalize_keys(keys: str) -> str:
    return "+".join(part.strip().lower() for part in keys.split("+") if part.strip())


def key_allowed(keys: str) -> bool:
    return normalize_keys(keys) in ALLOWED_KEY_COMBOS


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def url_allowed(url: str) -> bool:
    """Only https pages on the media hosts may be opened from LLM output."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and not parts.username
        and (parts.hostname or "").lower() in MEDIA_HOSTS
    )


def on_media_site(current_url: str) -> bool:
    """True if the active tab is a media page: generic clicks are only allowed there."""
    return host_of(current_url) in MEDIA_HOSTS


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


def sanitize_untrusted(text: str, limit: int = 80) -> str:
    """Make page-controlled text safe to quote in a prompt: one line, no markup, bounded."""
    cleaned = _CONTROL.sub(" ", text).replace("<", " ").replace(">", " ").replace("`", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]
