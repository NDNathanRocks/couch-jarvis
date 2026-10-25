"""The user's data files: safe writes, and a version record for future migrations (plan 5I).

The version lives in its own file (%APPDATA%\\CouchJarvis\\schema.json), not inside each data
file. An older app, which a user may reinstall over a newer one, reads its files with strict
models; a new key inside room.json would break it, a separate file never does.

Version 1 is the 0.3.0 layout, which every later version reads as it is. When a format changes,
add a step to MIGRATIONS (version -> function), bump DATA_VERSION, and test it against 0.3.0 data.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

DATA_VERSION = 1
SCHEMA_FILE = "schema.json"
# from-version -> step that upgrades the folder to from-version + 1
MIGRATIONS: dict[int, Callable[[Path], None]] = {}


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a file in one step: a crash or full disk never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def stored_version(folder: Path) -> int:
    """The data layout on disk: 0 = nothing yet, 1 = 0.3.0 (no schema file but data present)."""
    try:
        value = json.loads((folder / SCHEMA_FILE).read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        value = None
    if isinstance(value, int) and value >= 1:
        return value
    has_data = folder.is_dir() and any(p.suffix in (".json", ".env") for p in folder.iterdir())
    return 1 if has_data else 0


def migrate(folder: Path, migrations: dict[int, Callable[[Path], None]] | None = None) -> int:
    """Bring the data folder up to DATA_VERSION. Never downgrades; returns the version on disk.

    A failed step stops there (the data stays at the last good version) and is logged; the app
    then runs on what it can read, rather than not starting.
    """
    steps = MIGRATIONS if migrations is None else migrations
    target = max([DATA_VERSION, *(v + 1 for v in steps)])
    version = stored_version(folder)
    if version > target:
        log.warning("data folder is version %d, newer than this app (%d)", version, target)
        return version
    version = max(version, 1)
    while version < target:
        step = steps.get(version)
        if step is None:
            break
        try:
            step(folder)
        except Exception:  # noqa: BLE001 - keep running on the last good version
            log.exception("data migration %d -> %d failed", version, version + 1)
            break
        version += 1
    if folder.is_dir() or version > 1:
        with contextlib.suppress(OSError):
            atomic_write_text(folder / SCHEMA_FILE, json.dumps({"version": version}))
    return version
