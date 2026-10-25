"""A support bundle the user can send: what is wrong, without anything private.

It holds the version, the build, Windows, the Status rows, which settings are set (never their
values for keys) and the end of the logs, scrubbed like a crash report (no web addresses,
emails, user names, keys or tokens).
"""

from __future__ import annotations

import platform
from collections.abc import Iterable, Mapping
from pathlib import Path

from control.health import Row
from crash_reports import scrub_text

LOG_LINES = 300
SECRET_HINTS = ("KEY", "SECRET", "TOKEN", "DSN", "PASSWORD")


def settings_summary(values: Mapping[str, str]) -> list[str]:
    out = []
    for name in sorted(values):
        value = values[name]
        if any(h in name for h in SECRET_HINTS) or name.endswith("_URL"):
            out.append(f"{name}: {'set' if value else 'not set'}")
        else:
            out.append(f"{name}: {value}")
    return out


def tail(path: Path, lines: int = LOG_LINES) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [f"({path.name}: not found)"]
    return text.splitlines()[-lines:]


def build(
    *,
    version: str,
    channel: str,
    packaged: bool,
    status: Iterable[Row],
    settings: Mapping[str, str],
    logs: Mapping[str, list[str]],
    secrets: Iterable[str] = (),
) -> str:
    """The bundle as text. ``secrets`` are exact strings (keys, room secret) to blank out too."""
    parts = [
        "Armrest diagnostics",
        f"version {version} ({channel}{', Store package' if packaged else ''})",
        f"windows {platform.platform(terse=True)}",
        "",
        "STATUS",
        *(f"  [{r.tone}] {r.name}: {r.text}" for r in status),
        "",
        "SETTINGS",
        *(f"  {line}" for line in settings_summary(settings)),
    ]
    for name, lines in logs.items():
        parts += ["", f"LOG {name} (last {len(lines)} lines)", *lines]
    text = "\n".join(parts) + "\n"
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "<redacted>")
    return scrub_text(text)
