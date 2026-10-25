"""Read and change %APPDATA%\\CouchJarvis\\config.env without losing anything the user wrote.

Only the settings listed in FIELDS are offered in the window. Saving rewrites the one active
``CJ_NAME=value`` line of each changed setting (or appends it), keeps every comment and every other
line, and replaces the file atomically. An emptied setting is removed, so the app default applies.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["bool", "choice", "text", "secret", "number"]
_ACTIVE = re.compile(r"^\s*(CJ_[A-Z0-9_]+)\s*=\s*(.*?)\s*$")


@dataclass(frozen=True)
class Field:
    name: str  # CJ_...
    label: str
    kind: Kind
    default: str
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)
    section: str = "General"
    prefix: str = ""  # expected start of a key (a hint, not enforced: providers change formats)
    minimum: float | None = None
    maximum: float | None = None


FIELDS: tuple[Field, ...] = (
    Field(
        "CJ_GEMINI_API_KEY",
        "Gemini key",
        "secret",
        "",
        section="AI brain",
        prefix="AIza",
        help="Free from aistudio.google.com/apikey. Makes free-form commands fast on any PC.",
    ),
    Field(
        "CJ_ROUTER_PROVIDER",
        "Understand commands with",
        "choice",
        "auto",
        section="AI brain",
        choices=(
            ("auto", "Automatic (Gemini when a key is set)"),
            ("ollama", "This PC only"),
            ("gemini", "Gemini only"),
        ),
    ),
    Field(
        "CJ_STT_DEVICE",
        "Speech recognition runs on",
        "choice",
        "auto",
        section="Voice",
        choices=(("auto", "Automatic"), ("cuda", "NVIDIA graphics card"), ("cpu", "Processor")),
    ),
    Field(
        "CJ_GROQ_API_KEY",
        "Groq key",
        "secret",
        "",
        section="Voice",
        prefix="gsk_",
        help="Optional, free: cloud speech recognition for PCs without a graphics card.",
    ),
    Field(
        "CJ_ELEVENLABS_API_KEY",
        "ElevenLabs key",
        "secret",
        "",
        section="Voice",
        help="Optional: natural spoken replies (its free plan has a monthly allowance).",
    ),
    Field(
        "CJ_SUBTITLE_LANG",
        "Subtitle language",
        "text",
        "en",
        section="Voice",
        help='Two-letter code used by "turn on subtitles": en, fr, es...',
    ),
    Field(
        "CJ_REMOTE_INPUT",
        "Phones may use the touchpad and keyboard",
        "bool",
        "true",
        section="Phones",
        help="Turn off to limit phones to media and browser commands.",
    ),
    Field(
        "CJ_HUD_SCREEN_INDEX",
        "Show the QR code and messages on",
        "number",
        "0",
        section="TV",
        help="The screen that is your TV.",
        minimum=0,
        maximum=15,
    ),
    Field(
        "CJ_HUD_BADGE_HIDE_S",
        "Hide the TV status pill after (seconds)",
        "number",
        "10",
        section="TV",
        help="0 keeps it on screen.",
        minimum=0,
        maximum=3600,
    ),
    Field(
        "CJ_UPDATE_CHECK", "Tell me when a new version is out", "bool", "true", section="Privacy"
    ),
    Field(
        "CJ_CRASH_REPORTS",
        "Send anonymous crash reports",
        "bool",
        "false",
        section="Privacy",
        help="Only the error and where it happened: no names, addresses or what you watch.",
    ),
)
BY_NAME = {f.name: f for f in FIELDS}


def read_values(path: Path) -> dict[str, str]:
    """Active settings in the file (the last line wins, as for the app itself)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        m = _ACTIVE.match(line)
        if m:
            values[m[1]] = _unquote(m[2])
    return values


def current(path: Path) -> dict[str, str]:
    """Every offered setting with its value, or its default when the file does not set it."""
    found = read_values(path)
    return {f.name: found.get(f.name, f.default) for f in FIELDS}


def validate(name: str, value: str) -> str | None:
    """A problem to show next to the field, or None when the value is fine."""
    field = BY_NAME[name]
    value = value.strip()
    if field.kind == "bool" and value not in ("true", "false"):
        return "Choose on or off."
    if field.kind == "choice" and value not in {v for v, _ in field.choices}:
        return "Choose one of the options."
    if field.kind == "number":
        try:
            number = float(value)
        except ValueError:
            return "Enter a number."
        if field.minimum is not None and number < field.minimum:
            return f"At least {field.minimum:g}."
        if field.maximum is not None and number > field.maximum:
            return f"At most {field.maximum:g}."
    if field.kind == "secret" and value and (" " in value or len(value) < 8):
        return "That does not look like a key: paste it again."
    if name == "CJ_SUBTITLE_LANG" and not re.fullmatch(r"[a-z]{2}", value):
        return "Use a two-letter language code, like en or fr."
    return None


def save(path: Path, changes: Mapping[str, str]) -> list[str]:
    """Write the changed settings. Returns the names that changed (empty = nothing to do)."""
    for name, value in changes.items():
        if name not in BY_NAME:
            raise KeyError(name)
        problem = validate(name, value) if value.strip() else None
        if problem:
            raise ValueError(f"{name}: {problem}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    before = read_values(path)
    changed = [n for n, v in changes.items() if before.get(n, BY_NAME[n].default) != v.strip()]
    if not changed:
        return []
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        m = _ACTIVE.match(line)
        if m and m[1] in changed:
            if m[1] in written:
                continue  # a duplicate line: the value is written once
            written.add(m[1])
            value = changes[m[1]].strip()
            if value:
                out.append(f"{m[1]}={_quote(value)}")
            continue
        out.append(line)
    for name in changed:
        value = changes[name].strip()
        if name not in written and value:
            out.append(f"{name}={_quote(value)}")
    _write_atomic(path, "\n".join(out) + "\n")
    return changed


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r"[\s#\"']", value) else value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
