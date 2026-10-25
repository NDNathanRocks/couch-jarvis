"""Entrypoint of the packaged app (CouchJarvis.exe).

    CouchJarvis.exe                 supervisor: one instance, restarts the daemon if it dies
    CouchJarvis.exe --worker ...    the daemon itself (what the supervisor launches)

The supervisor has no window of its own. It keeps %LOCALAPPDATA%\\CouchJarvis\\logs, writes a
default %APPDATA%\\CouchJarvis\\config.env on first run, and always shows the TV overlay (the
QR that pairs a phone). Pairing links and secrets are never written to the log.
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from pathlib import Path

from config import resource_root, user_config_file

MUTEX_NAME = "Local\\CouchJarvisDaemon"
FIRST_DELAY_S = 2
MAX_DELAY_S = 60
HEALTHY_RUN_S = 60
QUICK_RESTART_MIN_S = 5
QUIET_LOGGERS = ("httpx", "httpx2", "httpcore")

_TEMPLATE = (
    "# Armrest settings. Lines starting with # are ignored; restart the app after editing.\n"
    "# CJ_RELAY_WS_URL=wss://...        cloud relay\n"
    "# CJ_PWA_BASE_URL=https://...      where the phone app is hosted\n"
)


def app_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "CouchJarvis" / "logs"


def ensure_user_config(target: Path, defaults: Path) -> bool:
    """Create the per-user config from the installer defaults. Never overwrites an existing one."""
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    text = defaults.read_text(encoding="utf-8") if defaults.is_file() else _TEMPLATE
    target.write_text(text, encoding="utf-8")
    return True


def after_exit(code: int, delay: float, ran_for: float) -> float | None:
    """What the supervisor does when the daemon exits: None = stop for good (the user quit),
    0 = start again now (a requested restart), else wait this long (a crash). A restart
    request seconds after starting is treated like a crash, so a bug can never spin the CPU."""
    from lifecycle import EXIT_QUIT, EXIT_RESTART

    if code == EXIT_QUIT:
        return None
    if code == EXIT_RESTART and ran_for >= QUICK_RESTART_MIN_S:
        return 0.0
    return delay


def next_delay(delay: float, ran_for: float) -> float:
    """Restart backoff: doubles while the daemon keeps dying fast, resets after a healthy run."""
    if ran_for >= HEALTHY_RUN_S:
        return FIRST_DELAY_S
    return min(delay * 2, MAX_DELAY_S)


def worker_args(extra: list[str]) -> list[str]:
    """Arguments for the daemon: the TV overlay is on, unless --no-hud asks for a headless run."""
    if "--no-hud" in extra:
        return [a for a in extra if a != "--no-hud"]
    return extra if "--hud" in extra else ["--hud", *extra]


ONE_SHOT_FLAGS = frozenset({"--rotate-secret"})


def without_one_shot(argv: list[str]) -> list[str]:
    """Flags that must only apply to the first launch, not to every crash-restart."""
    return [a for a in argv if a not in ONE_SHOT_FLAGS]


def configure_logging(name: str) -> None:
    """File logging, because a windowed app has no console (sys.stderr may even be None)."""
    log_dir = app_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{name}.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for chatty in QUIET_LOGGERS:  # one line per HTTP call (the Edge probe runs every 5 s)
        logging.getLogger(chatty).setLevel(logging.WARNING)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - lives for the process
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115


def _already_running() -> bool:
    """Single instance per Windows session (named mutex)."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, MUTEX_NAME)  # kept open until the process exits
    return bool(kernel32.GetLastError() == 183)  # ERROR_ALREADY_EXISTS


def toggle_running_qr(port: int | None = None) -> bool | None:
    """Ask the running daemon (loopback only) to show or hide the QR; None if it did not answer."""
    import json
    import urllib.request

    if port is None:
        from config import get_settings

        port = get_settings().lan_port
    req = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"http://127.0.0.1:{port}/api/qr/toggle", method="POST", data=b""
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
            return bool(json.loads(resp.read())["shown"])
    except (OSError, ValueError, KeyError):
        return None


def open_running_ui(page: str = "Status", port: int | None = None) -> bool:
    """Ask the running app (loopback only) to open its Control Center; False if it cannot."""
    import urllib.parse
    import urllib.request

    if port is None:
        from config import get_settings

        port = get_settings().lan_port
    query = urllib.parse.urlencode({"page": page})
    req = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"http://127.0.0.1:{port}/api/ui/open?{query}", method="POST", data=b""
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
            return bool(resp.status == 200)
    except (OSError, ValueError):
        return False


def run_worker(argv: list[str]) -> int:
    configure_logging("daemon")
    import main as daemon_main

    return daemon_main.main(worker_args(argv))


def run_supervisor(argv: list[str]) -> int:
    configure_logging("app")
    log = logging.getLogger("couch-jarvis.app")
    if _already_running():
        # Opening the app again (taskbar, Start menu) opens its window; an app that cannot
        # (no window, an older version mid-update) flips the pairing QR on the TV instead.
        if open_running_ui():
            log.info("the app is already running; opened its window; exiting")
        else:
            shown = toggle_running_qr()
            log.info("the app is already running; QR toggled (%s); exiting", shown)
        return 0
    ensure_user_config(user_config_file(), resource_root() / "installer" / "defaults.env")

    frozen = getattr(sys, "frozen", False)
    launcher = [sys.executable] if frozen else [sys.executable, str(Path(__file__).resolve())]
    delay: float = FIRST_DELAY_S
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    while True:
        started = time.monotonic()
        log.info("starting daemon")
        code = subprocess.call(  # noqa: S603 - our own binary
            [*launcher, "--worker", *argv], creationflags=flags
        )
        argv = without_one_shot(argv)
        ran = time.monotonic() - started
        wait = after_exit(code, delay, ran)
        if wait is None:
            log.info("daemon quit at the user's request; stopping")
            return 0
        if wait == 0:
            log.info("daemon asked to restart; starting it again")
            continue
        log.warning("daemon exited with code %s after %.0fs; restarting in %.0fs", code, ran, wait)
        time.sleep(wait)
        delay = next_delay(delay, ran)


def open_settings(target: Path, defaults: Path) -> int:
    """Open the Settings page of the running app, else the settings file in an editor."""
    if open_running_ui("Settings"):
        return 0
    ensure_user_config(target, defaults)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - the user's own settings file
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--worker"]:
        return run_worker(args[1:])
    if args[:1] == ["--open-settings"]:
        return open_settings(user_config_file(), resource_root() / "installer" / "defaults.env")
    return run_supervisor(args)


if __name__ == "__main__":
    sys.exit(main())
