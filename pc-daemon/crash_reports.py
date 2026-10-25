"""Opt-in crash reports (Sentry). Off unless the user turns them on (CJ_CRASH_REPORTS=true).

What is sent: the error type, the code lines it went through (file and function names of this
app), the app version and channel, and Windows' version. What is never sent: the PC or user name,
the IP address (Sentry is told not to keep it), local variables, log lines, what is playing, web
addresses, emails or anything that looks like a key or token (scrubbed below, also from the error
text itself). Reports go to the free Sentry plan; when its monthly quota is used up the extra
reports are dropped, never billed.
"""

from __future__ import annotations

import logging
import platform
import re
from typing import Any

from build_info import Channel, channel, crash_dsn
from version import __version__

log = logging.getLogger(__name__)

_URL = re.compile(r"\b(?:https?|wss?)://\S+", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_USER_PATH = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s'\"]+")
_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
_ALLOWED_TOP_LEVEL = frozenset(
    {
        "event_id",
        "timestamp",
        "platform",
        "level",
        "logger",
        "exception",
        "release",
        "environment",
        "contexts",
        "tags",
        "sdk",
        "message",
        "logentry",
        "fingerprint",
        "threads",
    }
)


def scrub_text(text: str) -> str:
    """Remove what could identify the user or what they were doing from one string."""
    text = _URL.sub("<url>", text)
    text = _EMAIL.sub("<email>", text)
    text = _USER_PATH.sub(r"\1<user>", text)
    return _TOKEN.sub("<redacted>", text)


def _scrub_frames(frames: list[dict[str, Any]]) -> None:
    for frame in frames:
        frame.pop("vars", None)
        for key in ("abs_path", "filename", "context_line"):
            if isinstance(frame.get(key), str):
                frame[key] = scrub_text(frame[key])
        for key in ("pre_context", "post_context"):
            frame.pop(key, None)


def scrub_event(event: dict[str, Any], _hint: Any = None) -> dict[str, Any] | None:
    """Sentry's before_send: keep only what helps fix the bug, scrubbed."""
    clean = {k: v for k, v in event.items() if k in _ALLOWED_TOP_LEVEL}
    for exc in (clean.get("exception") or {}).get("values", []) or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = scrub_text(exc["value"])
        _scrub_frames(((exc.get("stacktrace") or {}).get("frames")) or [])
    for thread in (clean.get("threads") or {}).get("values", []) or []:
        _scrub_frames(((thread.get("stacktrace") or {}).get("frames")) or [])
    if isinstance(clean.get("message"), str):
        clean["message"] = scrub_text(clean["message"])
    entry = clean.get("logentry")
    if isinstance(entry, dict):
        clean["logentry"] = {"message": scrub_text(str(entry.get("message", "")))}
    contexts = clean.get("contexts") or {}
    clean["contexts"] = {k: v for k, v in contexts.items() if k in ("os", "runtime")}
    return clean


def options(dsn: str, ch: Channel) -> dict[str, Any]:
    """Everything passed to sentry_sdk.init (kept separate so the privacy settings are tested)."""
    from sentry_sdk.integrations.logging import LoggingIntegration

    return {
        "dsn": dsn,
        "release": f"armrest@{__version__}",
        "environment": ch,
        "send_default_pii": False,
        "server_name": "pc",  # the default is the computer's name
        "include_local_variables": False,
        "include_source_context": False,
        "max_breadcrumbs": 0,
        "attach_stacktrace": False,
        "traces_sample_rate": 0.0,
        "auto_session_tracking": False,
        "before_send": scrub_event,
        "before_breadcrumb": lambda _crumb, _hint: None,
        # unhandled errors, plus errors the app logs at ERROR level; no log lines as breadcrumbs
        "integrations": [LoggingIntegration(level=None, event_level=logging.ERROR)],
        "default_integrations": True,
    }


def start(enabled: bool, dsn_override: str | None = None) -> bool:
    """Turn crash reporting on if the user asked for it and the build has somewhere to send it."""
    dsn = dsn_override or crash_dsn()
    if not enabled or not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(**options(dsn, channel()))
        sentry_sdk.set_tag("os", platform.platform(terse=True))
    except Exception:  # noqa: BLE001 - reporting must never stop the app
        log.warning("crash reports could not start", exc_info=True)
        return False
    log.info("crash reports are on (opt-in)")
    return True
