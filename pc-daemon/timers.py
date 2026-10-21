"""Sleep/notify timers shown on the TV HUD and phones. Fires a callback when one elapses."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from contracts import TimerInfo, new_id

log = logging.getLogger(__name__)

TimerCallback = Callable[[TimerInfo], Awaitable[None]]


class TimerService:
    def __init__(self, on_fire: TimerCallback, on_change: Callable[[], None] | None = None) -> None:
        self._on_fire = on_fire
        self._on_change = on_change
        self._timers: dict[str, TimerInfo] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, minutes: float, label: str, action: str = "notify") -> TimerInfo:
        if not 0 < minutes <= 24 * 60:
            raise ValueError("timer must be between 0 and 24 hours")
        info = TimerInfo(
            timer_id=new_id(), label=label, ends_at=time.time() + minutes * 60, action=action
        )
        self._timers[info.timer_id] = info
        self._tasks[info.timer_id] = asyncio.create_task(self._run(info, minutes * 60))
        self._changed()
        return info

    def cancel(self, timer_id: str) -> bool:
        task = self._tasks.pop(timer_id, None)
        if task is None:
            return False
        task.cancel()
        self._timers.pop(timer_id, None)
        self._changed()
        return True

    def list(self) -> list[TimerInfo]:
        return sorted(self._timers.values(), key=lambda t: t.ends_at)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._timers.clear()

    async def _run(self, info: TimerInfo, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self._timers.pop(info.timer_id, None)
        self._tasks.pop(info.timer_id, None)
        self._changed()
        try:
            await self._on_fire(info)
        except Exception:
            log.exception("timer callback failed: %s", info.timer_id)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()
