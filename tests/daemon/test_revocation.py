"""PC-side revocation: offline LAN tokens, live sockets, SSE streams and the owner-only API."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets

from config import Settings
from contracts import CmdButton, PairRequest, dump_message, envelope
from lan_auth import LOCKOUT_FAILURES, LOCKOUT_WINDOW_S, LanAuth
from local_api import create_app
from main import build_runtime
from room import RoomStore

KEY = "k" * 64


@pytest.fixture
def store(tmp_path: Path) -> RoomStore:
    s = RoomStore(tmp_path / "room.json")
    s.load_or_create()
    s.set_room_key(KEY)
    return s


# ------------------------------------------------------------------ LanAuth
def test_a_revoked_device_token_stops_working_and_stays_revoked_after_restart(
    store: RoomStore,
) -> None:
    lan = LanAuth(store)
    granted = lan.pair(store.identity.room_secret, "Pixel")
    assert granted is not None and lan.verify_token(granted.device_token) == granted.device_id
    lan.revoke(granted.device_id)
    assert lan.verify_token(granted.device_token) is None
    reloaded = LanAuth(RoomStore(store._path))  # noqa: SLF001 - simulates a daemon restart
    assert reloaded.verify_token(granted.device_token) is None


def test_revocation_survives_secret_rotation_and_does_not_touch_other_devices(
    store: RoomStore,
) -> None:
    lan = LanAuth(store)
    a = lan.pair(store.identity.room_secret, "A")
    b = lan.pair(store.identity.room_secret, "B")
    assert a is not None and b is not None
    lan.revoke(a.device_id)
    store.rotate_secret()
    assert lan.verify_token(a.device_token) is None
    assert lan.verify_token(b.device_token) == b.device_id


def test_pairing_is_locked_out_after_repeated_wrong_secrets_across_connections(
    store: RoomStore,
) -> None:
    now = [1000.0]
    lan = LanAuth(store, clock=lambda: now[0])
    for i in range(LOCKOUT_FAILURES):
        assert lan.pair(f"guess-{i}", "Attacker") is None  # each could be a fresh socket
    # even the CORRECT secret is refused while locked out: guessing cannot be paced by reconnecting
    assert lan.pair(store.identity.room_secret, "Owner") is None
    now[0] += LOCKOUT_WINDOW_S + 1
    assert lan.pair(store.identity.room_secret, "Owner") is not None


def test_failures_age_out_so_normal_typos_do_not_lock_you_out(store: RoomStore) -> None:
    now = [0.0]
    lan = LanAuth(store, clock=lambda: now[0])
    for _ in range(LOCKOUT_FAILURES - 1):
        lan.pair("typo", "Me")
        now[0] += LOCKOUT_WINDOW_S / 2
    assert lan.pair(store.identity.room_secret, "Me") is not None


# ------------------------------------------------------------------ live server
@dataclass
class Live:
    port: int
    store: RoomStore
    service: object
    auth: LanAuth

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"


@pytest.fixture
async def live(store: RoomStore) -> AsyncIterator[Live]:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    auth = LanAuth(store)
    rt.service.on_revoke = auth.revoke
    app = create_app(
        rt.service,
        rt.telemetry,
        token_verifier=auth.verify_token,
        pairing=auth.pair,
        trust_loopback=False,
        keepalive_s=0.2,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws_max_size=64 * 1024))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    yield Live(sock.getsockname()[1], store, rt.service, auth)
    server.should_exit = True
    await task
    await rt.service.stop()


async def pair(live: Live, name: str = "Pixel") -> tuple[str, str]:
    async with websockets.connect(live.ws + "?pair=1") as ws:
        msg = envelope(
            PairRequest,
            "dry-run",
            {"room_secret": live.store.identity.room_secret, "device_name": name},
            from_="p",
        )
        await ws.send(dump_message(msg))
        async with asyncio.timeout(5):
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "pair.granted":
                    return m["payload"]["device_id"], m["payload"]["device_token"]


async def test_revoking_closes_that_phones_live_socket_and_blocks_its_token(live: Live) -> None:
    a_id, a_token = await pair(live, "Ann")
    _b_id, b_token = await pair(live, "Bob")
    async with (
        websockets.connect(f"{live.ws}?token={a_token}") as ann,
        websockets.connect(f"{live.ws}?token={b_token}") as bob,
    ):
        await asyncio.wait_for(ann.recv(), 3)
        await asyncio.wait_for(bob.recv(), 3)
        live.service.revoke_device(a_id)  # type: ignore[attr-defined]
        with pytest.raises(websockets.ConnectionClosed) as closed:
            async with asyncio.timeout(5):
                while True:
                    await ann.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 4403
        # Bob is unaffected and still served
        press = envelope(
            CmdButton,
            "dry-run",
            {"command_id": "c", "idempotency_key": "k", "action": "next"},
            from_="b",
        )
        await bob.send(dump_message(press))
        async with asyncio.timeout(5):
            while json.loads(await bob.recv())["type"] != "cmd.result":
                pass
    with pytest.raises(
        websockets.exceptions.InvalidStatus
    ) as refused:  # old token no longer connects
        async with websockets.connect(f"{live.ws}?token={a_token}"):
            pass
    assert refused.value.response.status_code == 403  # upgrade refused before accept


async def test_a_revoked_phone_cannot_speak_even_if_its_socket_survives(live: Live) -> None:
    a_id, _ = await pair(live)
    reply = await live.service.handle_envelope(  # type: ignore[attr-defined]
        envelope(
            CmdButton,
            "r",
            {"command_id": "c", "idempotency_key": "k", "action": "next"},
            from_=a_id,
        ),
        a_id,
    )
    assert reply == []
    live.service.revoke_device(a_id)  # type: ignore[attr-defined]
    (err,) = await live.service.handle_envelope(  # type: ignore[attr-defined]
        envelope(
            CmdButton,
            "r",
            {"command_id": "c2", "idempotency_key": "k2", "action": "next"},
            from_=a_id,
        ),
        a_id,
    )
    assert err.payload.code == "revoked"


async def test_sse_stream_ends_when_its_device_is_revoked(live: Live) -> None:
    a_id, a_token = await pair(live)
    async with (
        httpx.AsyncClient(timeout=10) as client,
        client.stream("GET", f"{live.base}/events?token={a_token}") as r,
    ):
        assert r.status_code == 200
        lines = r.aiter_lines()
        await asyncio.wait_for(lines.__anext__(), 3)
        live.service.revoke_device(a_id)  # type: ignore[attr-defined]
        async with asyncio.timeout(5):
            async for _ in lines:  # the server must end the stream, not keep leaking events
                pass


async def test_owner_endpoints_work_from_the_pc_and_only_from_the_pc(live: Live) -> None:
    a_id, a_token = await pair(live, "Ann")
    # loopback trust is off in this fixture, so use ASGI transports to control the client address
    app = create_app(live.service, live.service.telemetry, token_verifier=live.auth.verify_token)  # type: ignore[attr-defined]
    remote = httpx.ASGITransport(app=app, client=("192.168.1.50", 4000))
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 4000))
    async with httpx.AsyncClient(transport=remote, base_url="http://192.168.1.9:8765") as c:
        # a paired friend with a perfectly valid token still cannot evict anyone
        for call in (
            c.get("/api/devices", params={"token": a_token}),
            c.post(f"/api/devices/{a_id}/revoke", params={"token": a_token}),
        ):
            assert (await call).status_code == 403
    async with httpx.AsyncClient(transport=local, base_url="http://127.0.0.1:8765") as c:
        assert (await c.post("/api/devices/dev bad/revoke")).status_code in (404, 422)
        assert (await c.post(f"/api/devices/{'x' * 80}/revoke")).status_code == 422
        ok = await c.post(f"/api/devices/{a_id}/revoke")
        assert ok.status_code == 200 and ok.json() == {"revoked": a_id}
        listing = (await c.get("/api/devices")).json()
        assert a_id in listing["revoked"]
    assert live.auth.verify_token(a_token) is None


async def test_revocation_is_published_so_the_cloud_relay_hears_about_it(live: Live) -> None:
    bus = live.service.bus  # type: ignore[attr-defined]
    async with bus.subscribe() as queue:
        live.service.revoke_device("dev-abc")  # type: ignore[attr-defined]
        event = await asyncio.wait_for(queue.get(), 2)
    assert event.type == "device.revoke" and json.loads(event.data)["payload"] == {
        "device_id": "dev-abc"
    }
