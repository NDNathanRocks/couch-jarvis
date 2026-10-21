from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import jwt
import pytest
import uvicorn
import websockets

from common import auth as relay_auth
from config import Settings
from contracts import CmdButton, PairRequest, dump_message, envelope
from lan_auth import LanAuth
from local_api import create_app
from main import build_runtime
from room import RoomStore

KEY = "k" * 64
OTHER_KEY = "o" * 64


@pytest.fixture
def store(tmp_path: Path) -> RoomStore:
    s = RoomStore(tmp_path / "room.json")
    s.load_or_create()
    return s


def test_room_key_persists_across_restarts_and_rotation(store: RoomStore) -> None:
    assert store.identity.room_key is None
    store.set_room_key(KEY)
    assert RoomStore(store._path).load_or_create().room_key == KEY  # noqa: SLF001
    rotated = store.rotate_secret()
    assert rotated.room_key == KEY  # a new QR secret does not lose the relay key
    assert KEY not in store._path.read_text(encoding="utf-8")  # noqa: SLF001


def test_lan_auth_is_not_ready_until_the_relay_key_arrives(store: RoomStore) -> None:
    lan = LanAuth(store)
    assert not lan.ready
    assert lan.verify_token("anything") is None
    assert lan.pair(store.identity.room_secret, "Pixel") is None
    store.set_room_key(KEY)
    assert lan.ready


def test_pairing_requires_the_exact_qr_secret(store: RoomStore) -> None:
    store.set_room_key(KEY)
    lan = LanAuth(store)
    assert lan.pair("wrong", "Pixel") is None
    assert lan.pair(store.identity.room_secret, "") is None
    assert lan.pair(store.identity.room_secret, "n" * 65) is None
    granted = lan.pair(store.identity.room_secret, "Pixel")
    assert granted is not None and granted.device_id.startswith("lan-")
    assert lan.verify_token(granted.device_token) == granted.device_id


def test_rotating_the_qr_secret_blocks_new_pairings_but_not_existing_tokens(
    store: RoomStore,
) -> None:
    store.set_room_key(KEY)
    lan = LanAuth(store)
    old_secret = store.identity.room_secret
    token = lan.pair(old_secret, "Pixel")
    assert token is not None
    store.rotate_secret()
    assert lan.pair(old_secret, "Intruder") is None
    assert lan.verify_token(token.device_token) == token.device_id


def test_relay_minted_tokens_verify_offline_on_the_pc(store: RoomStore) -> None:
    """The point of room.key: a token the *relay* signed is accepted on the LAN with no internet."""
    room_key = relay_auth.derive_room_key("deployment-master", store.identity.room_id)
    store.set_room_key(room_key)
    import time

    token, _ = relay_auth.mint_device_token(
        room_key, store.identity.room_id, "dev-cloud", time.time()
    )
    assert LanAuth(store).verify_token(token) == "dev-cloud"


def test_tokens_from_other_rooms_keys_expiry_and_alg_none_are_rejected(store: RoomStore) -> None:
    store.set_room_key(KEY)
    rid = store.identity.room_id
    lan = LanAuth(store, clock=lambda: 1_000.0)
    good = {"iss": "couch-jarvis", "sub": "dev-1", "rid": rid, "exp": 2_000}
    assert lan.verify_token(jwt.encode(good, KEY, algorithm="HS256")) == "dev-1"
    assert lan.verify_token(jwt.encode(good, OTHER_KEY, algorithm="HS256")) is None
    assert (
        lan.verify_token(jwt.encode({**good, "rid": "someone-elses-room"}, KEY, algorithm="HS256"))
        is None
    )
    assert lan.verify_token(jwt.encode({**good, "exp": 999}, KEY, algorithm="HS256")) is None
    assert lan.verify_token(jwt.encode({**good, "iss": "evil"}, KEY, algorithm="HS256")) is None
    assert lan.verify_token(jwt.encode(good, key=None, algorithm="none")) is None  # type: ignore[arg-type]
    assert lan.verify_token("not-a-jwt") is None


# ------------------------------------------------------- LAN websocket pairing, end to end
@dataclass
class Lan:
    ws_url: str
    store: RoomStore


@pytest.fixture
async def lan(tmp_path: Path) -> AsyncIterator[Lan]:
    store = RoomStore(tmp_path / "room.json")
    store.load_or_create()
    store.set_room_key(KEY)
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    auth = LanAuth(store)
    app = create_app(
        rt.service,
        rt.telemetry,
        token_verifier=auth.verify_token,
        pairing=auth.pair,
        trust_loopback=False,  # the test client is on loopback; a real phone is not
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    yield Lan(f"ws://127.0.0.1:{sock.getsockname()[1]}/ws", store)
    server.should_exit = True
    await task
    await rt.service.stop()


async def recv_until(ws: websockets.ClientConnection, type_: str) -> dict[str, object]:
    async with asyncio.timeout(5):
        while True:
            m = json.loads(await ws.recv())
            if m["type"] == type_:
                return m  # type: ignore[no-any-return]


def pair_msg(secret: str, name: str = "Pixel") -> str:
    return dump_message(
        envelope(PairRequest, "dry-run", {"room_secret": secret, "device_name": name}, from_="p")
    )


async def test_unpaired_socket_only_gets_pairing(lan: Lan) -> None:
    async with websockets.connect(lan.ws_url + "?pair=1") as ws:
        press = envelope(
            CmdButton,
            "dry-run",
            {"command_id": "c", "idempotency_key": "k", "action": "next"},
            from_="p",
        )
        await ws.send(dump_message(press))
        first = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert first["type"] == "error"  # no state.snapshot or events were served before pairing
        assert first["payload"]["code"] == "unauthorized" and first["reply_to"] == press.id
        await ws.send("{broken")
        assert (await recv_until(ws, "error"))["payload"]["code"] == "bad_message"  # type: ignore[index]


async def test_lan_pairing_then_commands_then_reconnect_with_token(lan: Lan) -> None:
    async with websockets.connect(lan.ws_url + "?pair=1&name=Pixel") as ws:
        await ws.send(pair_msg(lan.store.identity.room_secret))
        granted = await recv_until(ws, "pair.granted")
        token = granted["payload"]["device_token"]  # type: ignore[index]
        assert (await recv_until(ws, "state.snapshot"))["payload"]["pc_online"] is True  # type: ignore[index]
        press = envelope(
            CmdButton,
            "dry-run",
            {"command_id": "c", "idempotency_key": "k", "action": "next"},
            from_="p",
        )
        await ws.send(dump_message(press))
        assert (await recv_until(ws, "cmd.result"))["payload"]["ok"] is True  # type: ignore[index]
    async with websockets.connect(
        f"{lan.ws_url}?token={token}"
    ) as ws2:  # returning phone, no pairing
        assert (await recv_until(ws2, "state.snapshot"))["type"] == "state.snapshot"


async def test_wrong_secret_is_denied_and_repeated_guessing_is_cut_off(lan: Lan) -> None:
    async with websockets.connect(lan.ws_url + "?pair=1") as ws:
        for _ in range(5):
            await ws.send(pair_msg("guess"))
        denied = 0
        with pytest.raises(websockets.ConnectionClosed) as closed:
            async with asyncio.timeout(5):
                while True:
                    if json.loads(await ws.recv())["type"] == "pair.denied":
                        denied += 1
        assert denied == 5 and closed.value.rcvd is not None and closed.value.rcvd.code == 4403


async def test_a_browser_on_the_pc_itself_can_pair_with_pair_flag(tmp_path: Path) -> None:
    """Regression: loopback is trusted, which used to skip pairing and strand the PWA on 'Pairing…'."""
    store = RoomStore(tmp_path / "room.json")
    store.load_or_create()
    store.set_room_key("k" * 64)
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    auth = LanAuth(store)
    app = create_app(rt.service, rt.telemetry, token_verifier=auth.verify_token, pairing=auth.pair)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    try:
        uri = f"ws://127.0.0.1:{sock.getsockname()[1]}/ws?pair=1"
        async with websockets.connect(uri) as ws:
            await ws.send(pair_msg(store.identity.room_secret))
            granted = await recv_until(ws, "pair.granted")
            assert granted["payload"]["device_id"].startswith("lan-")  # type: ignore[index]
    finally:
        server.should_exit = True
        await task
        await rt.service.stop()
