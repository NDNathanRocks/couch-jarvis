from __future__ import annotations

import asyncio

import pytest

from contracts import TimerInfo
from timers import TimerService


async def test_timer_fires_callback_and_clears_itself() -> None:
    fired: list[TimerInfo] = []
    changes = [0]

    async def on_fire(info: TimerInfo) -> None:
        fired.append(info)

    svc = TimerService(on_fire, on_change=lambda: changes.__setitem__(0, changes[0] + 1))
    info = svc.start(0.0005, "tiny", "pause")  # 30 ms
    assert svc.list() == [info] and changes[0] == 1
    await asyncio.sleep(0.1)
    assert [f.timer_id for f in fired] == [info.timer_id]
    assert fired[0].action == "pause"
    assert svc.list() == [] and changes[0] == 2


async def test_cancel_prevents_firing() -> None:
    fired: list[TimerInfo] = []

    async def on_fire(info: TimerInfo) -> None:
        fired.append(info)

    svc = TimerService(on_fire)
    info = svc.start(0.0005, "x")
    assert svc.cancel(info.timer_id) is True
    assert svc.cancel(info.timer_id) is False
    await asyncio.sleep(0.1)
    assert fired == []


async def test_list_is_sorted_by_end_time() -> None:
    svc = TimerService(lambda info: asyncio.sleep(0))
    late = svc.start(30, "late")
    soon = svc.start(5, "soon")
    assert [t.label for t in svc.list()] == ["soon", "late"]
    await svc.shutdown()
    assert svc.list() == [] and late.timer_id != soon.timer_id


@pytest.mark.parametrize("minutes", [0, -1, 24 * 60 + 1])
async def test_invalid_durations_rejected(minutes: float) -> None:
    svc = TimerService(lambda info: asyncio.sleep(0))
    with pytest.raises(ValueError):
        svc.start(minutes, "bad")


async def test_failing_callback_does_not_crash() -> None:
    async def boom(info: TimerInfo) -> None:
        raise RuntimeError("boom")

    svc = TimerService(boom)
    svc.start(0.0005, "x")
    await asyncio.sleep(0.1)
    assert svc.list() == []
