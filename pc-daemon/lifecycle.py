"""How the daemon asks its supervisor (app_entry) to stop for good or to restart right away.

The supervisor restarts a daemon that exits for any other reason (a crash), with backoff, so
"Quit" needs its own exit code: without it the tray's Quit came back two seconds later.
"""

from __future__ import annotations

from collections.abc import Callable

EXIT_RESTART = 3  # start again at once (new identity, changed settings)
EXIT_QUIT = 10  # the user chose Quit: the supervisor stops too

_exit_code = 0
_stopper: Callable[[], None] | None = None


def set_stopper(stop: Callable[[], None] | None) -> None:
    """Register how to end the running daemon (stop the server / Qt loop)."""
    global _stopper
    _stopper = stop


def request_exit(code: int) -> None:
    """End the daemon with this exit code (EXIT_QUIT or EXIT_RESTART)."""
    global _exit_code
    _exit_code = code
    if _stopper is not None:
        _stopper()


def exit_code() -> int:
    return _exit_code


def reset() -> None:
    """Tests only."""
    global _exit_code, _stopper
    _exit_code, _stopper = 0, None
