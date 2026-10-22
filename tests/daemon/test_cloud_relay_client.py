"""Cloud client tests against an in-process fake relay (real websockets, no AWS)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from cloud_relay_client import CloudRelayClient, Status, backoff_delay
from contracts import (
    CmdButton,
    CmdText,
    ErrorMsg,
    Presence,
    RoomKey,
    StateSync,
    dump_message,
    envelope,
    parse_message,
)
from event_bus import EventBus
from room import RoomStore
from service import CommandService
from support import AgentEnv
from timers import TimerService


# --------------------------------------------------------------------------- backoff
def test_backoff_is_full_jitter_exponential_and_capped() -> None:
    assert backoff_delay(0, rng=lambda: 1.0) == 1.0
    assert backoff_delay(3, rng=lambda: 1.0) == 8.0
    assert backoff_delay(10, rng=lambda: 1.0) == 60.0  # capped
    assert backoff_delay(500, rng=lambda: 1.0) == 60.0  # no overflow
    assert backoff_delay(4, rng=lambda: 0.5) == 8.0
    assert backoff_delay(5, rng=lambda: 0.0) == 0.5  # never a hot loop


def test_backoff_stays_within_bounds_over_many_draws() -> None:
    for attempt in range(12):
        ceiling = min(60.0, 2.0**attempt)
        draws = [backoff_delay(attempt) for _ in range(200)]
        assert all(0.5 <= d <= max(0.5, ceiling) for d in draws)
        assert len({round(d, 3) for d in draws}) > 20  # actually jittered, not constant


# --------------------------------------------------------------------------- fake relay
@dataclass
class FakeRelay:
    url: str = ""
    connections: list[ServerConnection] = field(default_factory=list)
    headers_seen: list[dict[str, str]] = field(default_factory=list)
    paths_seen: list[str] = field(default_factory=list)
    received: list[dict[str, Any]] = field(default_factory=list)
    reject_with: int | None = None
    room_key: str = "relay-issued-room-key"

    async def handler(self, ws: ServerConnection) -> None:
        self.connections.append(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                if msg["type"] == "state.sync":  # PC hello -> key + presence, like relay_router
                    key = envelope(RoomKey, msg["room_id"], {"key": self.room_key, "key_id": "k1"})
                    await ws.send(dump_message(key))
        except ConnectionClosed:
            pass  # the PC dropped mid-reply (e.g. the test cancelled it)
        finally:
            self.connections.remove(ws)

    def gate(self, ws: ServerConnection, request: Request) -> Response | None:
        self.headers_seen.append({k.lower(): v for k, v in request.headers.items()})
        self.paths_seen.append(request.path)
        if self.reject_with:
            return ws.respond(self.reject_with, "nope\n")
        return None

    def types(self) -> list[str]:
        return [m["type"] for m in self.received]

    async def push(self, message: Any) -> None:
        for ws in list(self.connections):
            await ws.send(dump_message(message))

    async def drop_all(self) -> None:
        for ws in list(self.connections):
            await ws.close()


@pytest.fixture
async def relay() -> AsyncIterator[FakeRelay]:
    fake = FakeRelay()
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.gate) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        fake.url = f"ws://127.0.0.1:{port}/prod"
        yield fake


@dataclass
class Rig:
    client: CloudRelayClient
    service: CommandService
    store: RoomStore
    statuses: list[Status]
    sleeps: list[float]
    task: asyncio.Task[None]


async def until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


@pytest.fixture
async def rig(relay: FakeRelay, env: AgentEnv, tmp_path: Path) -> AsyncIterator[Rig]:
    bus = EventBus()
    timers = TimerService(lambda info: asyncio.sleep(0))
    store = RoomStore(tmp_path / "room.json")
    store.load_or_create()
    service = CommandService(
        room_id=store.identity.room_id,
        bus=bus,
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=timers,
        mode="lan",
        state_poll_s=3600,
    )
    await service.start()
    statuses: list[Status] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0.02)

    client = CloudRelayClient(
        url=relay.url,
        store=store,
        service=service,
        bus=bus,
        on_status=statuses.append,
        sleep=fake_sleep,
        rng=lambda: 1.0,
        stable_after_s=0.2,
        ping_interval_s=3600,
    )
    task = asyncio.create_task(client.run())
    yield Rig(client, service, store, statuses, sleeps, task)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await service.stop()


# --------------------------------------------------------------------------- behavior
async def test_handshake_credentials_hello_and_snapshot(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    ident = rig.store.identity
    h = relay.headers_seen[0]
    assert h["authorization"] == f"Bearer {ident.pc_key}"
    assert h["x-room-secret-hash"] == ident.room_secret_hash
    assert f"room={ident.room_id}" in relay.paths_seen[0] and "role=pc" in relay.paths_seen[0]
    assert ident.room_secret not in str(relay.paths_seen) + str(
        relay.headers_seen
    )  # never sent raw
    assert relay.types()[:2] == ["state.sync", "state.snapshot"]  # hello first, then full state
    assert rig.statuses == ["up"]


async def test_room_key_from_relay_is_persisted(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: rig.store.identity.room_key is not None)
    assert rig.store.identity.room_key == "relay-issued-room-key"
    assert RoomStore(rig.store._path).load_or_create().room_key == "relay-issued-room-key"  # noqa: SLF001


async def test_phone_command_runs_and_results_flow_back(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    cmd = envelope(
        CmdButton,
        rig.store.identity.room_id,
        {"command_id": "c1", "idempotency_key": "k1", "action": "next"},
        from_="dev-9",
    )
    await relay.push(cmd)
    await until(lambda: "cmd.result" in relay.types())
    ack = next(m for m in relay.received if m["type"] == "cmd.ack")
    assert ack["payload"]["command_id"] == "c1" and ack["payload"]["status"] == "accepted"
    assert next(m for m in relay.received if m["type"] == "cmd.result")["payload"]["ok"] is True
    assert "agent.progress" in relay.types()


async def test_relay_stamped_sender_becomes_the_commanding_device(
    rig: Rig, relay: FakeRelay
) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    cmd = envelope(
        CmdText,
        rig.store.identity.room_id,
        {"command_id": "c1", "idempotency_key": "k1", "text": "pause"},
        from_="dev-77",
    )
    await relay.push(cmd)
    await until(lambda: "cmd.result" in relay.types())
    spans = [r for r in rig.service.telemetry.recent() if r["name"] == "cmd.lifecycle"]
    assert spans[-1]["attributes"]["cj.device_id"] == "dev-77"


async def test_presence_updates_the_friends_list(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    presence = envelope(
        Presence,
        rig.store.identity.room_id,
        {
            "devices": [{"device_id": "dev-1", "name": "Ann", "online": True, "last_seen": 1.0}],
            "pc_online": True,
        },
        from_="relay",
    )
    await relay.push(presence)
    await until(lambda: [d.name for d in rig.service.state.state.devices] == ["Ann"])
    await until(lambda: "state.patch" in relay.types())  # change is pushed back out to phones


async def test_state_sync_from_a_phone_triggers_a_snapshot_reply(
    rig: Rig, relay: FakeRelay
) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    before = relay.types().count("state.snapshot")
    await relay.push(envelope(StateSync, rig.store.identity.room_id, {}, from_="dev-1"))
    await until(lambda: relay.types().count("state.snapshot") > before)


async def test_service_errors_carry_the_original_message_id(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    stray_chunk = envelope(
        CmdText,
        rig.store.identity.room_id,
        {"command_id": "x", "idempotency_key": "k", "text": "t"},
        from_="dev-1",
    ).model_dump(by_alias=True)
    stray_chunk.update(
        type="audio.chunk",
        id="orig-id-1",
        payload={"command_id": "never-started", "seq": 0, "b64": "!!not base64!!"},
    )
    for ws in relay.connections:
        await ws.send(json.dumps(stray_chunk))
    await until(lambda: "error" in relay.types())
    err = next(m for m in relay.received if m["type"] == "error")
    assert err["reply_to"] == "orig-id-1" and err["payload"]["code"] == "bad_audio"
    assert isinstance(parse_message(json.dumps(err)), ErrorMsg)


async def test_reconnects_with_backoff_and_resyncs_after_a_drop(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: rig.statuses == ["up"])
    snapshots = relay.types().count("state.snapshot")
    await relay.drop_all()
    await until(lambda: rig.statuses[-2:] == ["down", "up"])
    await until(lambda: relay.types().count("state.snapshot") > snapshots)
    assert relay.types().count("state.sync") >= 2  # said hello again
    assert rig.sleeps and rig.sleeps[0] <= 1.0  # first retry uses the smallest ceiling


async def test_repeated_failures_back_off_exponentially_then_reset(
    rig: Rig, relay: FakeRelay
) -> None:
    await until(lambda: rig.statuses == ["up"])
    relay.reject_with = 503  # relay unavailable
    await relay.drop_all()
    await until(lambda: len(rig.sleeps) >= 5)
    assert rig.sleeps[:5] == sorted(rig.sleeps[:5]) and rig.sleeps[4] > rig.sleeps[0]
    assert all(s <= 60 for s in rig.sleeps)
    relay.reject_with = None
    await until(lambda: rig.statuses[-1] == "up")


async def test_rejected_credentials_are_reported_and_slow_retried(
    rig: Rig, relay: FakeRelay
) -> None:
    await until(lambda: rig.statuses == ["up"])
    relay.reject_with = 401
    await relay.drop_all()
    await until(lambda: "rejected" in rig.statuses)
    await until(lambda: len(rig.sleeps) >= 1)
    assert rig.sleeps[-1] >= 0.5 and rig.client.status == "rejected"
    # a rejected PC jumps straight to the slow retry cadence (attempt >= 6 -> ceiling 60 s)
    assert backoff_delay(6, rng=lambda: 1.0) == 60.0


async def test_unreachable_relay_never_crashes_the_client(env: AgentEnv, tmp_path: Path) -> None:
    store = RoomStore(tmp_path / "room.json")
    store.load_or_create()
    service = CommandService(
        room_id="r",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda i: asyncio.sleep(0)),
        state_poll_s=3600,
    )
    with socket.socket() as probe:  # a port nothing listens on: refused immediately
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    statuses: list[Status] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0.01)

    client = CloudRelayClient(
        url=f"ws://127.0.0.1:{dead_port}/prod",
        store=store,
        service=service,
        bus=service.bus,
        telemetry=env.telemetry,
        on_status=statuses.append,
        sleep=fake_sleep,
    )
    task = asyncio.create_task(client.run())
    # Windows takes ~2 s to refuse a loopback connect, so allow time for two retries
    await until(lambda: len(sleeps) >= 2, timeout=15)
    assert not task.done()  # still retrying, not crashed
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert "up" not in statuses and client.status == "down"
    spans = [r for r in env.telemetry.recent() if r["name"] == "relay.reconnect"]
    assert len(spans) >= 2 and spans[0]["attributes"]["cj.attempt"] == 1
    assert spans[0]["attributes"]["error.type"]  # records why it is retrying


async def test_heartbeat_pings_keep_the_connection_warm(
    relay: FakeRelay, env: AgentEnv, tmp_path: Path
) -> None:
    store = RoomStore(tmp_path / "room.json")
    store.load_or_create()
    service = CommandService(
        room_id="r",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda i: asyncio.sleep(0)),
        state_poll_s=3600,
    )
    client = CloudRelayClient(
        url=relay.url, store=store, service=service, bus=service.bus, ping_interval_s=0.05
    )
    task = asyncio.create_task(client.run())
    await until(lambda: relay.types().count("ping") >= 2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_oversize_events_are_dropped_not_sent(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    huge = "x" * 40_000
    rig.service.state.update(
        timers=[{"timer_id": "t", "label": huge, "ends_at": 1.0, "action": "x"}]
    )
    await asyncio.sleep(0.2)
    assert all(len(json.dumps(m)) < 32 * 1024 for m in relay.received)
    assert rig.statuses == ["up"]  # connection survived


async def test_relay_revocation_list_is_enforced_locally_for_offline_use(
    rig: Rig, relay: FakeRelay
) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    presence = envelope(
        Presence,
        rig.store.identity.room_id,
        {"devices": [], "pc_online": True, "revoked": ["dev-evicted"]},
        from_="relay",
    )
    await relay.push(presence)
    await until(lambda: "dev-evicted" in rig.store.identity.revoked)
    assert "dev-evicted" in rig.service.revoked
    (err,) = await rig.service.handle_envelope(
        envelope(
            CmdText,
            rig.store.identity.room_id,
            {"command_id": "c", "idempotency_key": "k", "text": "pause"},
            from_="dev-evicted",
        ),
        "dev-evicted",
    )
    assert err.payload.code == "revoked"  # type: ignore[union-attr]
    assert RoomStore(rig.store._path).load_or_create().revoked == {"dev-evicted"}  # noqa: SLF001


async def test_owner_revocation_is_forwarded_to_the_relay(rig: Rig, relay: FakeRelay) -> None:
    await until(lambda: "state.snapshot" in relay.types())
    rig.service.revoke_device("dev-gone")
    await until(lambda: "device.revoke" in relay.types())
    sent = next(m for m in relay.received if m["type"] == "device.revoke")
    assert sent["payload"] == {"device_id": "dev-gone"}
