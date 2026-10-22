"""Helpers shared by the real-Edge integration tests (importable as ``edge_support``)."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Chromium spawns renderer/GPU children that outlive a plain kill() of the parent."""
    subprocess.run(  # noqa: S603
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
        capture_output=True,
        check=False,
    )
    proc.wait(10)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
