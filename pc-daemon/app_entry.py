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

_TEMPLATE = (
    "# CouchJarvis settings. Lines starting with # are ignored; restart the app after editing.\n"
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


def run_worker(argv: list[str]) -> int:
    configure_logging("daemon")
    import main as daemon_main

    return daemon_main.main(worker_args(argv))


def run_supervisor(argv: list[str]) -> int:
    configure_logging("app")
    log = logging.getLogger("couch-jarvis.app")
    if _already_running():
        log.info("another CouchJarvis is already running; exiting")
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
        log.warning("daemon exited with code %s after %.0fs; restarting in %.0fs", code, ran, delay)
        time.sleep(delay)
        delay = next_delay(delay, ran)


def open_settings(target: Path, defaults: Path) -> int:
    """Open the user's settings file in their editor (creating it first), then exit."""
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
