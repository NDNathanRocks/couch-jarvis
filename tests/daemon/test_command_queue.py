from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from command_queue import CommandQueue, CommandRequest, Priority
from contracts import CmdResultPayload


def make(
    i: str,
    *,
    kind: str = "text",
    device: str = "d1",
    key: str | None = None,
    action: str | None = None,
) -> CommandRequest:
    return CommandRequest.model_validate(
        {
            "command_id": f"cmd-{i}",
            "idempotency_key": key or f"key-{i}",
            "device_id": device,
            "kind": kind,
            "text": "x" if kind != "button" else None,
            "action": action,
        }
    )


class Recorder:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.order: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.gate: asyncio.Event | None = None

    async def handle(self, req: CommandRequest) -> CmdResultPayload:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.order.append(req.command_id)
            if self.gate is not None:
                await self.gate.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            return CmdResultPayload(command_id=req.command_id, ok=True, message="ok")
        finally:
            self.in_flight -= 1


@pytest.fixture
async def rec() -> Recorder:
    return Recorder()


async def drain(q: CommandQueue, n: int, results: list[str]) -> None:
    for _ in range(200):
        if len(results) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {len(results)}/{n} commands finished")


@pytest.fixture
async def queue_and_results(rec: Recorder) -> AsyncIterator[tuple[CommandQueue, list[str]]]:
    results: list[str] = []

    def on_result(req: CommandRequest, res: CmdResultPayload) -> None:
        results.append(f"{req.command_id}:{res.message}")

    q = CommandQueue(rec.handle, on_result=on_result)
    await q.start()
    yield q, results
    await q.stop()


async def test_priority_ordering_then_fifo(
    rec: Recorder, queue_and_results: tuple[CommandQueue, list[str]]
) -> None:
    q, results = queue_and_results
    rec.gate = asyncio.Event()
    q.submit(make("block"))  # occupies the worker
    await asyncio.sleep(0.02)
    q.submit(make("agent1"))
    q.submit(make("agent2"))
    q.submit(make("ctl1", kind="button", action="next"))
    q.submit(make("ctl2", kind="button", action="vol_up"))
    rec.gate.set()
    await drain(q, 5, results)
    assert rec.order == ["cmd-block", "cmd-ctl1", "cmd-ctl2", "cmd-agent1", "cmd-agent2"]


async def test_duplicate_idempotency_key_runs_once(
    rec: Recorder, queue_and_results: tuple[CommandQueue, list[str]]
) -> None:
    q, results = queue_and_results
    first = q.submit(make("a", key="same"))
    dup_inflight = q.submit(make("b", key="same"))
    assert first.status == "accepted"
    assert dup_inflight.status == "duplicate"
    await drain(q, 1, results)
    dup_done = q.submit(make("c", key="same"))
    assert dup_done.status == "duplicate"
    assert dup_done.cached_result is not None and dup_done.cached_result.ok
    await asyncio.sleep(0.05)
    assert rec.order == ["cmd-a"]


async def test_idempotency_entries_expire() -> None:
    now = [0.0]
    rec = Recorder()
    q = CommandQueue(rec.handle, idempotency_ttl_s=10, clock=lambda: now[0])
    await q.start()
    try:
        q.submit(make("a", key="k"))
        await asyncio.sleep(0.05)
        now[0] = 11.0
        assert q.submit(make("b", key="k")).status == "accepted"
        await asyncio.sleep(0.05)
        assert rec.order == ["cmd-a", "cmd-b"]
    finally:
        await q.stop()


async def test_concurrent_submits_are_serialized() -> None:
    rec = Recorder(delay=0.002)
    results: list[str] = []
    q = CommandQueue(rec.handle, on_result=lambda r, s: results.append(r.command_id))
    await q.start()
    try:

        async def friend(device: str) -> None:
            for i in range(10):
                q.submit(make(f"{device}-{i}", device=device))
                await asyncio.sleep(0)

        await asyncio.gather(*(friend(f"dev{n}") for n in range(5)))
        await drain(q, 50, results)
    finally:
        await q.stop()
    assert rec.max_in_flight == 1
    assert len(set(results)) == 50


async def test_emergency_cancels_inflight_agent_command() -> None:
    rec = Recorder()
    rec.gate = asyncio.Event()  # agent command hangs until released
    results: list[str] = []
    q = CommandQueue(
        rec.handle, on_result=lambda r, s: results.append(f"{r.command_id}:{s.message}")
    )
    await q.start()
    try:
        q.submit(make("slow"))
        await asyncio.sleep(0.02)
        assert q.driver == "d1"
        panic = q.submit(make("panic", kind="button", action="panic", device="d2"))
        assert panic.status == "queued"
        rec.gate.set()  # panic handler itself must not block
        await drain(q, 2, results)
    finally:
        await q.stop()
    assert results[0] == "cmd-slow:cancelled"
    assert results[1] == "cmd-panic:ok"


async def test_emergency_does_not_cancel_control_command() -> None:
    rec = Recorder()
    rec.gate = asyncio.Event()
    results: list[str] = []
    q = CommandQueue(
        rec.handle, on_result=lambda r, s: results.append(f"{r.command_id}:{s.message}")
    )
    await q.start()
    try:
        q.submit(make("ctl", kind="button", action="next"))
        await asyncio.sleep(0.02)
        q.submit(make("panic", kind="button", action="panic"))
        rec.gate.set()
        await drain(q, 2, results)
    finally:
        await q.stop()
    assert results == ["cmd-ctl:ok", "cmd-panic:ok"]


async def test_handler_exception_does_not_kill_worker() -> None:
    calls: list[str] = []

    async def handler(req: CommandRequest) -> CmdResultPayload:
        calls.append(req.command_id)
        if req.command_id == "cmd-bad":
            raise RuntimeError("boom")
        return CmdResultPayload(command_id=req.command_id, ok=True, message="ok")

    results: list[str] = []
    q = CommandQueue(handler, on_result=lambda r, s: results.append(f"{r.command_id}:{s.ok}"))
    await q.start()
    try:
        q.submit(make("bad"))
        q.submit(make("good"))
        await drain(q, 2, results)
    finally:
        await q.stop()
    assert results == ["cmd-bad:False", "cmd-good:True"]


async def test_depth_and_position_reporting() -> None:
    rec = Recorder()
    rec.gate = asyncio.Event()
    q = CommandQueue(rec.handle)
    await q.start()
    try:
        a = q.submit(make("a"))
        await asyncio.sleep(0.02)
        b = q.submit(make("b"))
        c = q.submit(make("c"))
        assert a.status == "accepted"
        assert (b.status, b.position) == ("queued", 1)
        assert c.position == 2
        assert q.depth == 3
        rec.gate.set()
    finally:
        await q.stop()


def test_default_priorities() -> None:
    assert make("a", kind="button", action="panic").effective_priority() == Priority.EMERGENCY
    assert make("a", kind="button", action="next").effective_priority() == Priority.CONTROL
    assert make("a", kind="voice").effective_priority() == Priority.AGENT
