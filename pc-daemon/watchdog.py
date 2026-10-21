"""Self-healing watchdog: keeps Edge alive on the CDP port with the persistent TV profile."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx

from telemetry import Telemetry

log = logging.getLogger(__name__)

CheckResult = Literal["healthy", "degraded", "relaunched", "breaker_open"]


def build_edge_command(edge_path: Path, profile_dir: Path, port: int) -> list[str]:
    return [
        str(edge_path),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def make_http_probe(cdp_url: str, timeout_s: float = 1.5) -> Callable[[], Awaitable[bool]]:
    async def probe() -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                return (await client.get(f"{cdp_url}/json/version")).status_code == 200
        except httpx.HTTPError:
            return False

    return probe


def _spawn_edge(edge_path: Path, profile_dir: Path, port: int) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # fixed argv, no shell
        build_edge_command(edge_path, profile_dir, port),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_edge_launcher(
    edge_path: Path, profile_dir: Path, port: int
) -> Callable[[], Awaitable[None]]:
    async def launch() -> None:
        await asyncio.to_thread(_spawn_edge, edge_path, profile_dir, port)

    return launch


class EdgeWatchdog:
    def __init__(
        self,
        probe: Callable[[], Awaitable[bool]],
        launch: Callable[[], Awaitable[None]],
        *,
        telemetry: Telemetry | None = None,
        interval_s: float = 5.0,
        failures_before_relaunch: int = 2,
        max_relaunches: int = 5,
        window_s: float = 120.0,
        cooldown_s: float = 300.0,
        startup_grace_s: float = 6.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        self._probe = probe
        self._launch = launch
        self._telemetry = telemetry or Telemetry(None)
        self._interval = interval_s
        self._threshold = failures_before_relaunch
        self._max = max_relaunches
        self._window = window_s
        self._cooldown = cooldown_s
        self._grace = startup_grace_s
        self._clock = clock
        self._sleep = sleep
        self._on_event = on_event
        self._failures = 0
        self._relaunches: deque[float] = deque()
        self._breaker_until = 0.0

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._breaker_until

    def _emit(self, event: str) -> None:
        log.warning("edge watchdog: %s", event)
        if self._on_event is not None:
            self._on_event(event)

    async def check_once(self, *, force: bool = False) -> CheckResult:
        """Probe CDP once; relaunch Edge after enough consecutive failures (or when forced)."""
        if self.breaker_open:
            return "breaker_open"
        if await self._probe():
            self._failures = 0
            return "healthy"
        self._failures += 1
        if not force and self._failures < self._threshold:
            return "degraded"

        now = self._clock()
        while self._relaunches and now - self._relaunches[0] > self._window:
            self._relaunches.popleft()
        if len(self._relaunches) >= self._max:
            self._breaker_until = now + self._cooldown
            self._emit(f"crash loop: {self._max} relaunches in {self._window:.0f}s; backing off")
            return "breaker_open"

        with self._telemetry.span(
            "watchdog.relaunch", attributes={"cj.relaunch_count": len(self._relaunches) + 1}
        ):
            await self._launch()
        self._relaunches.append(now)
        self._failures = 0
        self._emit("relaunched Edge")
        return "relaunched"

    async def ensure_edge(self) -> bool:
        """Immediate repair path for the CDP manager: relaunch now and wait for the port."""
        result = await self.check_once(force=True)
        if result == "healthy":
            return True
        if result == "relaunched":
            deadline = self._clock() + self._grace
            while self._clock() < deadline:
                if await self._probe():
                    return True
                await self._sleep(0.25)
        return False

    async def run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                log.exception("watchdog check failed")
            await self._sleep(self._interval)
