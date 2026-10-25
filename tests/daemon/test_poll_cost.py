"""The PC polls the browser often only while a phone is watching (relay messages cost money)."""

from __future__ import annotations

import asyncio

from contracts import DevicePresence
from event_bus import EventBus
from service import CommandService
from support import AgentEnv
from timers import TimerService


async def test_polls_slowly_with_no_phone_and_at_once_when_one_arrives(env: AgentEnv) -> None:
    service = CommandService(
        room_id="r1",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda info: asyncio.sleep(0)),
        mode="lan",
        state_poll_s=0.05,
        idle_poll_s=3600,
    )
    polls: list[float] = []
    real = service.refresh_state

    async def counting() -> None:
        polls.append(asyncio.get_running_loop().time())
        await real()

    service.refresh_state = counting  # type: ignore[method-assign]
    await service.start()
    try:
        await asyncio.sleep(0.3)
        assert len(polls) == 1  # the start-up poll, then the long idle wait
        service.set_cloud_devices(
            [DevicePresence(device_id="dev-1", name="Ann", online=True, last_seen=1.0)]
        )
        await asyncio.sleep(0.3)
        assert len(polls) >= 4  # woken at once, then every 50 ms while watched
        service.set_cloud_devices([])
        await asyncio.sleep(0.15)  # one more fast wait may finish
        settled = len(polls)
        await asyncio.sleep(0.3)
        assert len(polls) == settled  # back to the idle cadence
    finally:
        await service.stop()
