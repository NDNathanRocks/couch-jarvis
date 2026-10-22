"""Adversarial tests for the daemon's browser-facing boundary (found during the Phase 3 QA pass)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
import websockets
from websockets.exceptions import InvalidStatus

from config import Settings
from contracts import CmdButton, dump_message, envelope
from guard import host_allowed, hostname_of, origin_allowed
from local_api import create_app
from main import Runtime, build_runtime


@dataclass
class Live:
    port: int
    rt: Runtime

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"


@pytest.fixture
def live_app() -> object:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    return create_app(rt.service, rt.telemetry)


@pytest.fixture
async def live() -> AsyncIterator[Live]:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    app = create_app(
        rt.service, rt.telemetry, allowed_origins=["https://pwa.example"], keepalive_s=0.2
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws_max_size=64 * 1024))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    yield Live(sock.getsockname()[1], rt)
    server.should_exit = True
    await task
    await rt.service.stop()


def press(i: int = 0, action: str = "panic") -> str:
    msg = envelope(
        CmdButton,
        "dry-run",
        {"command_id": f"c{i}", "idempotency_key": f"k{i}", "action": action},
        from_="x",
    )
    return dump_message(msg)


# ------------------------------------------------------------------ host / origin primitives
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8765", True),
        ("localhost:8765", True),
        ("192.168.1.20:8765", True),
        ("[::1]:8765", True),
        ("[fe80::1]", True),
        ("tv-pc.local:8765", True),
        ("attacker.example:8765", False),
        ("attacker.example", False),
        ("127.0.0.1.evil.com", False),
        ("localhost.evil.com", False),
        ("", False),
    ],
)
def test_host_allowlist(header: str, expected: bool) -> None:
    assert host_allowed(header) is expected


def test_hostname_parsing() -> None:
    assert hostname_of("Example.COM:80") == "example.com"
    assert hostname_of("[::1]:8765") == "::1"
    assert hostname_of("::1") == "::1"


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://127.0.0.1:8765", True),  # same-origin
        ("http://192.168.1.20:8765", False),  # different host than the Host header
        ("https://pwa.example", True),  # allow-listed
        ("https://evil.example", False),
        ("null", False),
        ("file://", False),
        ("chrome-extension://abc", False),
        ("http://127.0.0.1:9999", False),  # same host, other port = other origin
    ],
)
def test_origin_policy(origin: str, expected: bool) -> None:
    assert origin_allowed(origin, "127.0.0.1:8765", frozenset({"https://pwa.example"})) is expected


# ------------------------------------------------------------------ cross-site WebSocket hijack
async def test_websocket_from_a_foreign_web_page_is_refused(live: Live) -> None:
    """A page on evil.example can open ws://127.0.0.1/ws (browsers do not apply CORS to it)."""
    with pytest.raises(InvalidStatus) as exc:
        async with websockets.connect(live.ws, origin="https://evil.example"):
            pass
    assert exc.value.response.status_code == 403
    assert live.rt.service.queue.depth == 0


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1:1", "file://"])
async def test_other_hostile_origins_are_refused_too(live: Live, origin: str) -> None:
    with pytest.raises(InvalidStatus):
        async with websockets.connect(live.ws, origin=origin):
            pass


async def test_same_origin_pwa_and_allowlisted_origin_still_work(live: Live) -> None:
    for origin in (f"http://127.0.0.1:{live.port}", "https://pwa.example"):
        async with websockets.connect(live.ws, origin=origin) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert first["type"] == "state.snapshot"


async def test_non_browser_clients_without_origin_still_work(live: Live) -> None:
    async with websockets.connect(live.ws) as ws:
        await ws.send(press())
        async with asyncio.timeout(5):
            while json.loads(await ws.recv())["type"] != "cmd.result":
                pass


# ------------------------------------------------------------------ DNS rebinding and CSRF
async def test_dns_rebinding_host_is_refused_for_http_and_sse(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        for path in ("/api/metrics", "/healthz", "/events"):
            r = await client.get(f"{live.http}{path}", headers={"Host": "attacker.example:8765"})
            assert r.status_code == 403, path
        post = await client.post(
            f"{live.http}/api/command",
            headers={"Host": "attacker.example"},
            json={"type": "cmd.button", "action": "panic", "idempotency_key": "rebind"},
        )
        assert post.status_code == 403


async def test_rebinding_host_is_refused_for_websocket(live: Live) -> None:
    # A raw handshake: client libraries insist on sending their own Host header.
    reader, writer = await asyncio.open_connection("127.0.0.1", live.port)
    handshake = "\r\n".join(
        [
            "GET /ws HTTP/1.1",
            "Host: attacker.example:8765",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
    )
    writer.write(handshake.encode())
    await writer.drain()
    status_line = await asyncio.wait_for(reader.readline(), 3)
    writer.close()
    assert b" 403 " in status_line, status_line


async def test_cross_site_http_posts_are_refused_even_when_simple(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        for content_type in ("text/plain", "application/json", "application/x-www-form-urlencoded"):
            r = await client.post(
                f"{live.http}/api/command",
                headers={"Origin": "https://evil.example", "Content-Type": content_type},
                content='{"type":"cmd.button","action":"panic","idempotency_key":"csrf"}',
            )
            assert r.status_code == 403, content_type
    assert live.rt.service.queue.depth == 0


async def test_cors_preflight_only_succeeds_for_allowed_origins(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        ok = await client.options(
            f"{live.http}/api/command",
            headers={"Origin": "https://pwa.example", "Access-Control-Request-Method": "POST"},
        )
        bad = await client.options(
            f"{live.http}/api/command",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
    assert ok.headers.get("access-control-allow-origin") == "https://pwa.example"
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers


async def test_events_cannot_be_read_cross_site(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{live.http}/events", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------------------ response hardening
async def test_security_headers_on_every_response(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{live.http}/healthz")
        err = await client.get(f"{live.http}/api/metrics", headers={"Host": "evil.example"})
    for resp in (r,):
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["referrer-policy"] == "no-referrer"  # pairing URLs carry a secret
        assert resp.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert err.status_code == 403


async def test_sse_stream_keeps_its_headers_and_is_not_compressed(live: Live) -> None:
    async with httpx.AsyncClient() as client, client.stream("GET", f"{live.http}/events") as r:
        assert r.headers["cache-control"].startswith("no-cache")
        assert r.headers["x-accel-buffering"] == "no"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "content-encoding" not in r.headers


# ------------------------------------------------------------------ resource exhaustion
async def test_oversized_websocket_frames_close_the_connection(live: Live) -> None:
    async with websockets.connect(live.ws, max_size=None) as ws:
        await asyncio.wait_for(ws.recv(), 3)
        await ws.send("x" * (256 * 1024))
        with pytest.raises(websockets.ConnectionClosed) as closed:
            async with asyncio.timeout(5):
                while True:
                    await ws.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1009
    assert live.rt.service.queue.depth == 0  # the daemon is unharmed


async def test_a_legit_audio_sized_frame_still_fits(live: Live) -> None:
    chunk = "A" * (24 * 1024)
    msg = {
        "v": 1,
        "type": "audio.chunk",
        "id": "a",
        "ts": 1.0,
        "room_id": "r",
        "from": "x",
        "reply_to": None,
        "payload": {"command_id": "c", "seq": 0, "b64": chunk},
    }
    assert len(json.dumps(msg)) < 64 * 1024
    async with websockets.connect(live.ws) as ws:
        await asyncio.wait_for(ws.recv(), 3)
        await ws.send(json.dumps(msg))
        ping = {**msg, "type": "ping", "id": "p", "payload": {}}
        await ws.send(json.dumps(ping))  # the connection survived the big frame and still answers
        reply = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert reply["type"] == "pong"


async def test_http_command_cannot_impersonate_another_device(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{live.http}/api/command",
            json={
                "type": "cmd.button",
                "action": "next",
                "idempotency_key": "imp",
                "device_id": "dev-victim",
            },
        )
    assert r.status_code == 202
    await asyncio.sleep(0.2)
    spans = [s for s in live.rt.telemetry.recent() if s["name"] == "cmd.lifecycle"]
    assert spans and spans[-1]["attributes"]["cj.device_id"] == "local"  # the field is ignored


def test_no_route_leaks_a_dependency_alias_as_a_query_parameter(live_app: object) -> None:
    """Regression: a locally defined Annotated alias is not resolvable under postponed annotations,
    so FastAPI silently turned auth dependencies into required query params (422 on every call)."""
    schema = live_app.openapi()  # type: ignore[attr-defined]
    for path, methods in schema["paths"].items():
        for method, spec in methods.items():
            for param in spec.get("parameters", []):
                assert param["name"] not in ("_", "device", "request"), (path, method, param)
                if param["in"] == "query":
                    assert param["name"] in ("token",), (path, method, param)
