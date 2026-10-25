"""Which build this is: ``dev`` (source), ``direct`` (the downloadable installer) or ``store``.

The channel is baked in by installer\\build.ps1 as ``installer/build_info.json`` next to the
bundled defaults. Code asks *what a channel may do* (the helpers below), never the channel name,
so the rules for each channel live in this one file:

- ``store``: the Microsoft Store updates the app and sells the licence. No GitHub update notice,
  no script that downloads and runs another installer, only services licensed for commercial use.
- ``direct``: the installer from the website. Its own update notice and the brain setup script,
  same licensing rules as the Store.
- ``dev``: everything, including hobby-only services (``edge-tts``).
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from config import resource_root

Channel = Literal["dev", "direct", "store"]
CHANNELS: tuple[Channel, ...] = ("dev", "direct", "store")
APPMODEL_ERROR_NO_PACKAGE = 15700


def read_channel(path: Path) -> Channel:
    """The channel recorded in a build_info.json; ``dev`` if there is none or it is unreadable."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("channel")
    except (OSError, ValueError, AttributeError):
        return "dev"
    return value if value in CHANNELS else "dev"


@lru_cache(maxsize=1)
def channel() -> Channel:
    return read_channel(resource_root() / "installer" / "build_info.json")


@lru_cache(maxsize=1)
def is_packaged() -> bool:
    """True when running from an MSIX package (it has a package identity)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    length = wintypes.UINT(0)
    code = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    return bool(code != APPMODEL_ERROR_NO_PACKAGE)


@lru_cache(maxsize=1)
def package_family_name() -> str | None:
    """The MSIX package family (``Name_publisherhash``), or None outside a package."""
    if not is_packaged():
        return None
    import ctypes
    from ctypes import wintypes

    length = wintypes.UINT(0)
    ctypes.windll.kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), None)
    buf = ctypes.create_unicode_buffer(length.value)
    if ctypes.windll.kernel32.GetCurrentPackageFamilyName(ctypes.byref(length), buf) != 0:
        return None
    return buf.value


def edge_profile_dir(default: Path, family: str | None, local_appdata: Path) -> Path:
    """Where the TV browser keeps its profile.

    Inside an MSIX, Windows redirects a *new* ``%LOCALAPPDATA%\\CouchJarvis`` to the package's
    private copy, but Edge's helper processes run outside the package and would write half the
    profile to the real folder (seen in the 5A spike). Naming the package's folder directly gives
    every Edge process the same files. A profile left by the direct installer (a real folder, so
    not redirected) keeps being used, so the streaming sign-ins survive a move to the Store app.
    """
    if family is None:
        return default
    own = local_appdata / "Packages" / family / "LocalCache" / "Local" / "CouchJarvis"
    own /= "edge-profile"
    if own.exists() or not default.exists():
        return own
    return default


def shows_update_notice(ch: Channel | None = None) -> bool:
    """The Store updates Store apps: they must not point people at other downloads."""
    return (ch or channel()) != "store"


def runs_brain_installer(ch: Channel | None = None) -> bool:
    """May the app run the script that downloads and installs Ollama? Not from the Store."""
    return (ch or channel()) != "store"


def allows_hobby_services(ch: Channel | None = None) -> bool:
    """Services whose terms do not allow a commercial product (``edge-tts``): source builds only."""
    return (ch or channel()) == "dev"
