"""Randomized interleavings of submit / cancel / duplicate under load (Phase 3, async boundaries)."""

from __future__ import annotations

import asyncio
import random

import pytest

from command_queue import CommandQueue, CommandRequest
from contracts import CmdResultPayload


@pytest.mark.parametrize("seed", range(8))
async def test_every_accepted_command_gets_exactly_one_result_and_never_overlaps(seed: int) -> None:
    rng = random.Random(seed)
    in_flight = peak = 0
    results: dict[str, list[str]] = {}

    async def handler(req: CommandRequest) -> CmdResultPayload:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(rng.uniform(0, 0.004))
            return CmdResultPayload(command_id=req.command_id, ok=True, message="ok")
        finally:
            in_flight -= 1

    def on_result(req: CommandRequest, res: CmdResultPayload) -> None:
        results.setdefault(req.command_id, []).append(res.message)

    q = CommandQueue(handler, max_pending=40, on_result=on_result)
    await q.start()
    accepted: set[str] = set()
    try:

        async def client(device: str) -> None:
            for i in range(60):
                kind = rng.choice(["text", "text", "voice", "button", "panic", "dup"])
                key = f"{device}-{i if kind != 'dup' else rng.randrange(i + 1)}"
                req = CommandRequest(
                    command_id=f"{device}-{i}-{kind}",
                    idempotency_key=key,
                    device_id=device,
                    kind="button"
                    if kind in ("button", "panic")
                    else ("voice" if kind == "voice" else "text"),
                    text="x" if kind in ("text", "voice", "dup") else None,
                    action=("panic" if kind == "panic" else "next")
                    if kind in ("button", "panic")
                    else None,
                )
                ack = q.submit(req)
                if ack.status in ("accepted", "queued"):
                    accepted.add(req.command_id)
                await asyncio.sleep(rng.uniform(0, 0.002))

        await asyncio.gather(*(client(f"dev{n}") for n in range(8)))
        async with asyncio.timeout(10):
            while q.depth or len(results) < len(accepted):
                await asyncio.sleep(0.01)
    finally:
        await q.stop()

    assert peak == 1, "two commands ran at once"
    missing = accepted - results.keys()
    assert not missing, f"commands that were accepted but never answered: {sorted(missing)[:5]}"
    doubled = {c: r for c, r in results.items() if len(r) != 1}
    assert not doubled, f"commands answered more than once: {doubled}"
    assert set(results) <= accepted  # nothing answered that was never accepted
