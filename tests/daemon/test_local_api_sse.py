"""Live-server tests: SSE streaming and WebSockets need a real socket (ASGI transports buffer)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
import websockets

from config import Settings
from contracts import CmdButton, Ping, StatePatch, dump_message, envelope
from local_api import create_app
from main import Runtime, build_runtime


@dataclass
class Live:
    url: str
    rt: Runtime


@pytest.fixture
async def live() -> AsyncIterator[Live]:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    app = create_app(rt.service, rt.telemetry, keepalive_s=0.2)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    yield Live(f"http://127.0.0.1:{sock.getsockname()[1]}", rt)
    server.should_exit = True
    await task
    await rt.service.stop()


class Sse:
    """Minimal SSE reader over a streaming httpx response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self._lines = response.aiter_lines()

    async def frame(self, timeout: float = 3.0) -> dict[str, str]:
        out: dict[str, str] = {}
        async with asyncio.timeout(timeout):
            async for line in self._lines:
                if line == "":
                    if out:
                        return out
                    continue
                if line.startswith(":"):
                    return {"comment": line[1:].strip()}
                key, _, value = line.partition(": ")
                out[key] = value
        raise AssertionError("stream ended")

    async def until(self, event: str, timeout: float = 5.0) -> list[dict[str, str]]:
        seen: list[dict[str, str]] = []
        async with asyncio.timeout(timeout):
            while True:
                f = await self.frame(timeout)
                seen.append(f)
                if f.get("event") == event:
                    return seen


async def post(client: httpx.AsyncClient, url: str, **body: Any) -> httpx.Response:
    return await client.post(f"{url}/api/command", json=body)


async def test_first_frame_is_snapshot_with_streaming_headers(live: Live) -> None:
    async with httpx.AsyncClient() as client, client.stream("GET", f"{live.url}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in r.headers["cache-control"]
        assert r.headers["x-accel-buffering"] == "no"
        assert "content-encoding" not in r.headers  # never gzip an event stream
        first = await Sse(r).frame()
    assert first["event"] == "state.snapshot"
    body = json.loads(first["data"])
    assert body["type"] == "state.snapshot" and body["payload"]["pc_online"] is True


async def test_command_streams_ack_progress_result(live: Live) -> None:
    async with httpx.AsyncClient() as client, client.stream("GET", f"{live.url}/events") as r:
        sse = Sse(r)
        assert (await sse.frame())["event"] == "state.snapshot"
        resp = await post(client, live.url, type="cmd.text", text="pause", idempotency_key="k1")
        assert resp.status_code == 202 and resp.json()["status"] == "accepted"
        frames = await sse.until("cmd.result")
    events = [f["event"] for f in frames]
    assert events[0] == "cmd.ack"
    assert "agent.progress" in events and events[-1] == "cmd.result"
    result = json.loads(frames[-1]["data"])
    assert result["payload"]["ok"] is True
    ids = [int(f["id"]) for f in frames if "id" in f]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)  # strictly increasing event ids


async def test_duplicate_idempotency_key_over_http(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        first = await post(client, live.url, type="cmd.text", text="pause", idempotency_key="same")
        second = await post(client, live.url, type="cmd.text", text="pause", idempotency_key="same")
    assert first.json()["status"] == "accepted" and second.json()["status"] == "duplicate"


async def test_last_event_id_replays_missed_events(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        await post(client, live.url, type="cmd.button", action="next", idempotency_key="k1")
        async with asyncio.timeout(3):
            while live.rt.service.queue.depth:
                await asyncio.sleep(0.01)
        headers = {"Last-Event-ID": "0"}
        async with client.stream("GET", f"{live.url}/events", headers=headers) as r:
            sse = Sse(r)
            assert (await sse.frame())["event"] == "state.snapshot"
            frames = await sse.until("cmd.result")
        assert "cmd.ack" in [f["event"] for f in frames]
        # resuming from the newest id replays nothing: next frame is a keepalive
        latest = live.rt.service.bus.last_seq
        async with client.stream(
            "GET", f"{live.url}/events", headers={"Last-Event-ID": str(latest)}
        ) as r:
            sse = Sse(r)
            await sse.frame()
            assert (await sse.frame()) == {"comment": "keepalive"}


async def test_keepalive_comments_flow_while_idle(live: Live) -> None:
    async with httpx.AsyncClient() as client, client.stream("GET", f"{live.url}/events") as r:
        sse = Sse(r)
        await sse.frame()
        assert (await sse.frame()) == {"comment": "keepalive"}
        assert (await sse.frame()) == {"comment": "keepalive"}


async def test_api_validation_errors(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        assert (
            await post(client, live.url, type="cmd.text", idempotency_key="k")
        ).status_code == 422
        assert (
            await post(client, live.url, type="cmd.button", idempotency_key="k")
        ).status_code == 422
        assert (
            await post(client, live.url, type="cmd.button", action="rm_rf", idempotency_key="k")
        ).status_code == 422
        assert (await post(client, live.url, type="nope", idempotency_key="k")).status_code == 422


async def test_metrics_and_health(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        await post(client, live.url, type="cmd.button", action="next", idempotency_key="m1")
        async with asyncio.timeout(3):
            while live.rt.telemetry.rollup()["commands"] == 0:
                await asyncio.sleep(0.01)
        metrics = (await client.get(f"{live.url}/api/metrics")).json()
        health = (await client.get(f"{live.url}/healthz")).json()
    assert metrics["commands"] >= 1 and metrics["latency_ms"]["p50"] is not None
    assert health["ok"] is True


async def test_non_loopback_clients_need_a_valid_token(live: Live) -> None:
    app = create_app(
        live.rt.service, live.rt.telemetry, token_verifier=lambda t: "dev1" if t == "good" else None
    )
    transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://pc") as client:
        assert (await client.get("/api/metrics")).status_code == 401
        assert (await client.get("/api/metrics", params={"token": "bad"})).status_code == 401
        assert (await client.get("/api/metrics", params={"token": "good"})).status_code == 200
        ok = await client.get("/api/metrics", headers={"Authorization": "Bearer good"})
        assert ok.status_code == 200
        assert (await client.get("/healthz")).status_code == 200  # health is public
        cmd = await client.post(
            "/api/command", json={"type": "cmd.text", "text": "pause", "idempotency_key": "x"}
        )
        assert cmd.status_code == 401


async def test_cors_only_allows_configured_origins(live: Live) -> None:
    app = create_app(live.rt.service, live.rt.telemetry, allowed_origins=["https://remote.example"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://pc"
    ) as client:
        good = await client.get("/healthz", headers={"Origin": "https://remote.example"})
        bad = await client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert good.headers.get("access-control-allow-origin") == "https://remote.example"
    assert "access-control-allow-origin" not in bad.headers


async def test_lan_websocket_round_trip(live: Live) -> None:
    uri = live.url.replace("http", "ws") + "/ws?name=Pixel"
    async with websockets.connect(uri) as ws:
        snap = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert snap["type"] == "state.snapshot"
        press = envelope(
            CmdButton,
            "dry-run",
            {"command_id": "w1", "idempotency_key": "wk", "action": "panic"},
            from_="phone",
        )
        await ws.send(dump_message(press))
        seen: list[str] = []
        async with asyncio.timeout(5):
            while "cmd.result" not in seen:
                seen.append(json.loads(await ws.recv())["type"])
        assert "cmd.ack" in seen
        await ws.send(dump_message(envelope(Ping, "dry-run", {}, from_="phone")))
        async with asyncio.timeout(3):
            while True:
                if json.loads(await ws.recv())["type"] == "pong":
                    break
    async with asyncio.timeout(3):  # disconnect marks the device offline
        while not any(not d.online for d in live.rt.service.state.state.devices):
            await asyncio.sleep(0.01)


async def test_lan_websocket_rejects_garbage_and_forbidden_types(live: Live) -> None:
    uri = live.url.replace("http", "ws") + "/ws"
    async with websockets.connect(uri) as ws:
        await asyncio.wait_for(ws.recv(), 3)  # snapshot
        await ws.send("{not json")
        await ws.send(
            dump_message(envelope(StatePatch, "dry-run", {"volume": 0, "rev": 5}, from_="phone"))
        )
        codes: list[str] = []
        async with asyncio.timeout(3):
            while len(codes) < 2:
                m = json.loads(await ws.recv())
                if m["type"] == "error":
                    codes.append(m["payload"]["code"])
    assert codes == ["bad_message", "forbidden_type"]
    assert live.rt.service.state.state.volume == 50


async def test_loopback_trust_can_be_switched_off(live: Live) -> None:
    app = create_app(live.rt.service, live.rt.telemetry, trust_loopback=False)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://pc") as client:
        assert (await client.get("/api/metrics")).status_code == 401
    trusted = create_app(live.rt.service, live.rt.telemetry)
    transport = httpx.ASGITransport(app=trusted, client=("127.0.0.1", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://pc") as client:
        assert (await client.get("/api/metrics")).status_code == 200
