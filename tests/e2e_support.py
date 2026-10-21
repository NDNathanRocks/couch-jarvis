"""Real-browser (Microsoft Edge via Playwright) end-to-end harness. Opt in with: pytest -m e2e.

Lives in tests/ (on pythonpath) so test modules can import it; fixtures are re-exported by
tests/e2e/conftest.py."""

from __future__ import annotations

import asyncio
import http.server
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from room import RoomStore

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web-remote" / "dist"
SHOTS = Path(os.environ.get("E2E_SHOTS", ROOT / "tests" / "e2e" / ".shots"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Daemon:
    port: int
    room_file: Path
    proc: subprocess.Popen[bytes] | None = None
    log: Path | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pair_url(self) -> str:
        ident = RoomStore(self.room_file).load_or_create()
        return f"{self.url}/?room={ident.room_id}&secret={ident.room_secret}"

    def start(self) -> None:
        env = {**os.environ, "CJ_ROOM_FILE": str(self.room_file), "PYTHONUNBUFFERED": "1"}
        log = open(self.log, "ab") if self.log else subprocess.DEVNULL  # noqa: SIM115
        self.proc = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(ROOT / "pc-daemon" / "main.py"),
                "--dry-run",
                "--port",
                str(self.port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("daemon did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        self.proc = None


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    room = tmp_path / "room.json"
    store = RoomStore(room)
    store.load_or_create()
    store.set_room_key("e" * 64)  # dry-run has no relay to hand one out
    d = Daemon(free_port(), room, log=tmp_path / "daemon.log")
    d.start()
    yield d
    d.stop()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="msedge",
            headless=True,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        )
        yield b
        b.close()


PHONES = {
    "320x568 (iPhone SE 1)": {"width": 320, "height": 568},
    "360x740 (small Android)": {"width": 360, "height": 740},
    "390x844 (iPhone 14)": {"width": 390, "height": 844},
    "430x932 (iPhone Pro Max)": {"width": 430, "height": 932},
    "844x390 (landscape)": {"width": 844, "height": 390},
}


@pytest.fixture
def phone(browser: Browser) -> Iterator[Callable[..., Page]]:
    contexts: list[BrowserContext] = []

    def make(viewport: dict[str, int] | None = None, **kw: Any) -> Page:
        ctx = browser.new_context(
            viewport=viewport or {"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            locale="en-US",
            **kw,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
        return page

    yield make
    for ctx in contexts:
        ctx.close()


def pair(page: Page, daemon: Daemon) -> None:
    page.goto(daemon.pair_url)
    page.get_by_role("button", name="Play / Pause").wait_for(timeout=15_000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('button')].some(b => b.getAttribute('aria-label') === 'Play / Pause' && !b.disabled)",
        timeout=15_000,
    )


# ---------------------------------------------------------------------------- scripted stub server
@dataclass
class StubServer:
    """Serves the built PWA and a scriptable /ws: 429 for the first N upgrades, then a tiny relay."""

    port: int = field(default_factory=free_port)
    throttle_first: int = 0
    reject_commands: bool = False
    attempts: list[float] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
    _stop: asyncio.Event | None = None
    _thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        ready = threading.Event()

        async def main() -> None:
            import json

            from websockets.asyncio.server import serve
            from websockets.datastructures import Headers
            from websockets.http11 import Response

            self._stop = asyncio.Event()

            async def handler(ws: Any) -> None:
                snapshot = {
                    "v": 1,
                    "type": "state.snapshot",
                    "id": "s",
                    "ts": 1,
                    "room_id": "r",
                    "from": "pc",
                    "reply_to": None,
                    "payload": {"pc_online": True, "mode": "lan", "rev": 1, "volume": 40},
                }
                await ws.send(json.dumps(snapshot))
                async for raw in ws:
                    msg = json.loads(raw)
                    self.messages.append(msg)
                    if msg["type"] == "cmd.button" and self.reject_commands:
                        ack = {
                            "v": 1,
                            "type": "cmd.ack",
                            "id": f"a{len(self.messages)}",
                            "ts": 1,
                            "room_id": "r",
                            "from": "pc",
                            "reply_to": None,
                            "payload": {
                                "command_id": msg["payload"]["command_id"],
                                "status": "rejected",
                                "reason": "rate_limited",
                            },
                        }
                        await ws.send(json.dumps(ack))

            def gate(connection: Any, request: Any) -> Any:
                path = request.path.split("?")[0]
                if path == "/ws":
                    self.attempts.append(time.monotonic())
                    if len(self.attempts) <= self.throttle_first:
                        body = b"slow down\n"
                        hdrs = Headers(
                            [
                                ("Retry-After", "2"),
                                ("Content-Type", "text/plain"),
                                ("Content-Length", str(len(body))),
                            ]
                        )
                        return Response(429, "Too Many Requests", hdrs, body)
                    return None
                target = DIST / (path.lstrip("/") or "index.html")
                if not target.is_file() or DIST not in target.resolve().parents:
                    target = DIST / "index.html"
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                data = target.read_bytes()
                headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(data)))])
                return Response(200, "OK", headers, data)

            async with serve(handler, "127.0.0.1", self.port, process_request=gate):
                ready.set()
                await self._stop.wait()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(main())

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(10)

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)


@pytest.fixture
def stub() -> Iterator[StubServer]:
    s = StubServer()
    yield s
    s.stop()


class OtherSite(http.server.ThreadingHTTPServer):
    """A different origin standing in for any website the user happens to visit."""


@pytest.fixture
def evil_site() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"<!doctype html><title>innocent cat pictures</title><h1>cats</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = OtherSite(("127.0.0.1", free_port()), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
