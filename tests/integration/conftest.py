"""One headless Edge with a CDP port per test module, shared by the real-Edge tests."""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from edge_support import EDGE, free_port, kill_tree
from watchdog import build_edge_command


@pytest.fixture(scope="module")
def edge_port() -> Iterator[int]:
    if not EDGE.exists():
        pytest.skip("Microsoft Edge is not installed")
    port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="cj-edge-"))
    cmd = [
        *build_edge_command(EDGE, profile, port),
        "--headless=new",
        "--autoplay-policy=no-user-gesture-required",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        kill_tree(proc)
        pytest.fail("Edge did not expose the CDP port")
    yield port
    kill_tree(proc)
