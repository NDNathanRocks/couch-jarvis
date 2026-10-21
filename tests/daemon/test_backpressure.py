"""Flood, slow-consumer and resource-exhaustion behaviour (Phase 3)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import pytest

from command_queue import CommandQueue, CommandRequest
from contracts import (
    AudioBegin,
    AudioChunk,
    AudioEnd,
    CmdButton,
    CmdResultPayload,
    ErrorMsg,
    Ping,
    envelope,
)
from event_bus import EventBus
from service import (
    MAX_AUDIO_PER_DEVICE,
    MAX_AUDIO_TOTAL,
    MAX_CONCURRENT_STT,
    CommandService,
    TokenBucket,
)
from stt_engine import STTEngine
from support import AgentEnv
from timers import TimerService


def req(i: int, device: str = "d1", action: str | None = None) -> CommandRequest:
    return CommandRequest(
        command_id=f"c{i}",
        idempotency_key=f"k{i}",
        device_id=device,
        kind="button",
        action=action or "next",  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ queue back-pressure
async def test_queue_rejects_beyond_its_backlog_but_never_the_panic_button() -> None:
    gate = asyncio.Event()

    async def handler(r: CommandRequest) -> CmdResultPayload:
        await gate.wait()
        return CmdResultPayload(command_id=r.command_id, ok=True, message="ok")

    q = CommandQueue(handler, max_pending=5)
    await q.start()
    try:
        q.submit(req(1000))  # let the worker pick the first command up (it blocks on the gate)
        await asyncio.sleep(0.02)
        statuses = [q.submit(req(i)).status for i in range(12)]
        assert statuses.count("rejected") == 12 - 5  # 1 running (separate) + 5 queued
        rejected = q.submit(req(99))
        assert rejected.status == "rejected" and rejected.reason == "queue_full"
        assert q.submit(req(100, action="panic")).status in (
            "queued",
            "accepted",
        )  # always admitted
        gate.set()
        async with asyncio.timeout(3):
            while q.depth:
                await asyncio.sleep(0.01)
        # a rejected key was never recorded, so the phone can simply retry once things drain
        assert q.submit(req(99)).status == "accepted"
    finally:
        await q.stop()


async def test_memory_stays_bounded_under_a_flood() -> None:
    async def handler(r: CommandRequest) -> CmdResultPayload:
        await asyncio.sleep(0.001)
        return CmdResultPayload(command_id=r.command_id, ok=True, message="ok")

    q = CommandQueue(handler, max_pending=50, idempotency_max=100)
    await q.start()
    try:
        for i in range(20_000):
            q.submit(req(i, device=f"dev{i % 7}"))
        assert q.depth <= 51
        assert len(q._seen) <= 100  # noqa: SLF001 - the idempotency cache is capped too
    finally:
        await q.stop()


# ------------------------------------------------------------------ per-device rate limiting
def test_token_bucket_refills_over_time() -> None:
    now = [0.0]
    bucket = TokenBucket(rate=2.0, burst=3, clock=lambda: now[0])
    assert [bucket.allow("a") for _ in range(4)] == [True, True, True, False]
    assert bucket.allow("b")  # other keys are independent
    now[0] += 1.0  # +2 tokens
    assert [bucket.allow("a") for _ in range(3)] == [True, True, False]
    now[0] += 3600
    assert bucket.allow("a")


def test_token_bucket_forgets_idle_keys() -> None:
    now = [0.0]
    bucket = TokenBucket(rate=1.0, burst=1, clock=lambda: now[0])
    for i in range(2000):
        bucket.allow(f"k{i}")
        now[0] += 0.1
    assert len(bucket._state) < 1100  # noqa: SLF001


@pytest.fixture
async def svc(env: AgentEnv) -> AsyncIterator[CommandService]:
    service = CommandService(
        room_id="r",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda i: asyncio.sleep(0)),
        stt=None,
        state_poll_s=3600,
    )
    await service.start()
    yield service
    await service.stop()


def acks(svc: CommandService) -> list[dict[str, object]]:
    return [json.loads(e.data)["payload"] for e in svc.bus.replay_since(0) if e.type == "cmd.ack"]


async def test_a_flooding_phone_is_rate_limited_and_told_why(svc: CommandService) -> None:
    for i in range(30):
        svc.submit(req(i, device="spammer"))
    statuses = [a["status"] for a in acks(svc)]
    assert statuses.count("rejected") >= 15
    limited = next(a for a in acks(svc) if a["status"] == "rejected")
    assert limited["reason"] == "rate_limited"


async def test_one_phone_flooding_does_not_starve_another(svc: CommandService) -> None:
    for i in range(50):
        svc.submit(req(i, device="spammer"))
    svc.submit(req(1000, device="friend"))
    friend = [a for a in acks(svc) if a["command_id"] == "c1000"]
    assert friend and friend[0]["status"] in ("accepted", "queued")


async def test_panic_always_gets_through_the_rate_limit(svc: CommandService) -> None:
    for i in range(40):
        svc.submit(req(i, device="spammer"))
    svc.submit(req(5000, device="spammer", action="panic"))
    panic = [a for a in acks(svc) if a["command_id"] == "c5000"]
    assert panic and panic[0]["status"] != "rejected"


# ------------------------------------------------------------------ slow consumers
async def test_dropped_events_are_flagged_exactly_once() -> None:
    from contracts import CmdAck

    bus = EventBus(subscriber_queue_size=3)
    async with bus.subscribe() as fast, bus.subscribe() as slow:
        for i in range(2):
            bus.publish(envelope(CmdAck, "r", {"command_id": f"c{i}", "status": "accepted"}))
        while not fast.empty():
            fast.get_nowait()  # the fast consumer keeps up
        assert not bus.consume_lag(slow)
        for i in range(10):
            bus.publish(envelope(CmdAck, "r", {"command_id": f"x{i}", "status": "accepted"}))
            while not fast.empty():
                fast.get_nowait()
        assert not bus.consume_lag(fast)
        assert bus.consume_lag(slow) is True  # it lost events: it must resync
        assert bus.consume_lag(slow) is False  # and is only told once


async def test_websocket_pump_resends_state_after_dropping_events(env: AgentEnv) -> None:
    """End to end: a stalled phone connection gets a fresh snapshot instead of stale state."""
    import socket

    import uvicorn
    import websockets

    from local_api import create_app

    bus = EventBus(subscriber_queue_size=4)
    service = CommandService(
        room_id="r",
        bus=bus,
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda i: asyncio.sleep(0)),
        state_poll_s=3600,
    )
    await service.start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(service, env.telemetry), log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{sock.getsockname()[1]}/ws") as ws:
            await asyncio.wait_for(ws.recv(), 3)  # initial snapshot
            await asyncio.sleep(0.05)
            for i in range(60):  # burst far beyond the 4-slot queue, in one event-loop turn
                service.state.update(volume=i % 100)
            service.state.update(volume=77)
            seen: list[dict[str, object]] = []
            async with asyncio.timeout(5):
                while not (
                    seen
                    and seen[-1].get("type") == "state.patch"
                    and seen[-1]["payload"].get("volume") == 77
                ):  # type: ignore[union-attr]
                    seen.append(json.loads(await ws.recv()))
            snapshots = [m for m in seen if m["type"] == "state.snapshot"]
            assert snapshots, "a lagging client must be resynced with a full snapshot"
            assert snapshots[-1]["payload"]["volume"] in range(100)  # type: ignore[index]
    finally:
        server.should_exit = True
        await task
        await service.stop()


# ------------------------------------------------------------------ audio buffers
class SlowStt:
    name = "faster-whisper"

    def __init__(self) -> None:
        self.running = self.peak = 0

    async def transcribe(self, audio: bytes, mime: str) -> str:
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.05)
        self.running -= 1
        return "pause"


@pytest.fixture
async def voice(env: AgentEnv) -> AsyncIterator[tuple[CommandService, SlowStt]]:
    stt = SlowStt()
    service = CommandService(
        room_id="r",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda i: asyncio.sleep(0)),
        stt=STTEngine(stt, None),
        state_poll_s=3600,
    )
    await service.start()
    yield service, stt
    await service.stop()


def begin(cid: str) -> AudioBegin:
    return envelope(
        AudioBegin,
        "r",
        {"command_id": cid, "idempotency_key": f"k-{cid}", "mime": "audio/webm"},
        from_="x",
    )


def chunk(cid: str, seq: int = 0) -> AudioChunk:
    return envelope(
        AudioChunk,
        "r",
        {"command_id": cid, "seq": seq, "b64": base64.b64encode(b"abc").decode()},
        from_="x",
    )


def end(cid: str, n: int = 1) -> AudioEnd:
    return envelope(
        AudioEnd, "r", {"command_id": cid, "total_chunks": n, "duration_ms": 100}, from_="x"
    )


async def test_audio_buffers_are_namespaced_by_sender(
    voice: tuple[CommandService, SlowStt],
) -> None:
    svc, _ = voice
    await svc.handle_envelope(begin("shared-id"), "alice")
    # Bob guesses Alice command id: he can neither add to nor finish her recording...
    for msg in (chunk("shared-id"), end("shared-id")):
        (err,) = await svc.handle_envelope(msg, "bob")
        assert isinstance(err, ErrorMsg) and err.payload.code == "unknown_audio"
    # ...and his own begin with the same id does not overwrite hers
    await svc.handle_envelope(begin("shared-id"), "bob")
    assert ("alice", "shared-id") in svc._audio and ("bob", "shared-id") in svc._audio  # noqa: SLF001
    assert await svc.handle_envelope(chunk("shared-id"), "alice") == []


async def test_audio_buffers_are_capped_per_device_and_overall(
    voice: tuple[CommandService, SlowStt],
) -> None:
    svc, _ = voice
    for i in range(50):
        await svc.handle_envelope(begin(f"a{i}"), "alice")
    assert sum(1 for k in svc._audio if k[0] == "alice") == MAX_AUDIO_PER_DEVICE  # noqa: SLF001
    assert ("alice", "a49") in svc._audio  # the newest survive  # noqa: SLF001
    for d in range(30):
        await svc.handle_envelope(begin("x"), f"dev{d}")
    assert len(svc._audio) <= MAX_AUDIO_TOTAL  # noqa: SLF001


async def test_stt_concurrency_is_bounded_however_many_phones_talk(
    voice: tuple[CommandService, SlowStt],
) -> None:
    svc, stt = voice
    for d in range(6):
        await svc.handle_envelope(begin("v"), f"dev{d}")
        await svc.handle_envelope(chunk("v"), f"dev{d}")
        await svc.handle_envelope(end("v"), f"dev{d}")
    async with asyncio.timeout(5):
        while len([e for e in svc.bus.replay_since(0) if e.type == "cmd.result"]) < 6:
            await asyncio.sleep(0.02)
    assert stt.peak <= MAX_CONCURRENT_STT


async def test_ping_is_cheap_and_never_rate_limited(svc: CommandService) -> None:
    for _ in range(200):
        (pong,) = await svc.handle_envelope(envelope(Ping, "r", {}, from_="x"), "d")
        assert pong.type == "pong"


async def test_button_press_envelope_goes_through_the_limiter(svc: CommandService) -> None:
    for i in range(30):
        await svc.handle_envelope(
            envelope(
                CmdButton,
                "r",
                {"command_id": f"c{i}", "idempotency_key": f"k{i}", "action": "next"},
                from_="x",
            ),
            "phone",
        )
    assert any(a["status"] == "rejected" for a in acks(svc))
