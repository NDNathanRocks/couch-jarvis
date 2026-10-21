from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from watchdog import EdgeWatchdog, build_edge_command, make_edge_launcher


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    """Watchdog with a scriptable probe, a launch counter and a manual clock."""

    def __init__(self, **kwargs: float) -> None:
        self.clock = Clock()
        self.up = True
        self.launches = 0
        self.events: list[str] = []
        self.up_after_launch = False

        async def probe() -> bool:
            return self.up

        async def launch() -> None:
            self.launches += 1
            if self.up_after_launch:
                self.up = True

        async def sleep(seconds: float) -> None:
            self.clock.now += seconds

        self.dog = EdgeWatchdog(
            probe, launch, clock=self.clock, sleep=sleep, on_event=self.events.append, **kwargs
        )


async def test_healthy_edge_is_left_alone() -> None:
    rig = Rig()
    assert await rig.dog.check_once() == "healthy"
    assert rig.launches == 0


async def test_relaunch_needs_consecutive_failures() -> None:
    rig = Rig(failures_before_relaunch=2)
    rig.up = False
    assert await rig.dog.check_once() == "degraded"  # one blip is tolerated
    rig.up = True
    assert await rig.dog.check_once() == "healthy"  # recovery resets the counter
    rig.up = False
    assert await rig.dog.check_once() == "degraded"
    assert await rig.dog.check_once() == "relaunched"
    assert rig.launches == 1 and rig.events == ["relaunched Edge"]


async def test_crash_loop_breaker_opens_after_five_relaunches_in_two_minutes() -> None:
    rig = Rig(failures_before_relaunch=1, max_relaunches=5, window_s=120, cooldown_s=300)
    rig.up = False
    results = []
    for _ in range(6):
        results.append(await rig.dog.check_once())
        rig.clock.now += 10
    assert results == ["relaunched"] * 5 + ["breaker_open"]
    assert rig.launches == 5 and rig.dog.breaker_open
    assert any("crash loop" in e for e in rig.events)
    # while open it neither probes into a relaunch nor launches
    rig.clock.now += 100
    assert await rig.dog.check_once() == "breaker_open" and rig.launches == 5


async def test_breaker_closes_after_cooldown() -> None:
    rig = Rig(failures_before_relaunch=1, max_relaunches=2, window_s=120, cooldown_s=300)
    rig.up = False
    for _ in range(3):
        await rig.dog.check_once()
    assert rig.dog.breaker_open
    rig.clock.now += 301
    assert not rig.dog.breaker_open
    assert await rig.dog.check_once() == "relaunched"


async def test_slow_restarts_do_not_trip_the_breaker() -> None:
    rig = Rig(failures_before_relaunch=1, max_relaunches=5, window_s=120)
    rig.up = False
    for _ in range(8):  # one relaunch every 3 minutes is a flaky browser, not a crash loop
        assert await rig.dog.check_once() == "relaunched"
        rig.clock.now += 180
    assert not rig.dog.breaker_open


async def test_ensure_edge_relaunches_immediately_and_waits_for_the_port() -> None:
    rig = Rig(failures_before_relaunch=5, startup_grace_s=6)  # threshold ignored when forced
    rig.up = False
    rig.up_after_launch = True
    assert await rig.dog.ensure_edge() is True
    assert rig.launches == 1


async def test_ensure_edge_reports_failure_if_port_never_opens() -> None:
    rig = Rig(startup_grace_s=1)
    rig.up = False
    assert await rig.dog.ensure_edge() is False
    assert rig.launches == 1


async def test_ensure_edge_is_a_noop_when_healthy() -> None:
    rig = Rig()
    assert await rig.dog.ensure_edge() is True and rig.launches == 0


def test_edge_command_uses_cdp_port_and_persistent_profile() -> None:
    cmd = build_edge_command(Path("C:/edge/msedge.exe"), Path("C:/tv-profile"), 9222)
    assert cmd[0] == str(Path("C:/edge/msedge.exe"))
    assert "--remote-debugging-port=9222" in cmd
    assert f"--user-data-dir={Path('C:/tv-profile')}" in cmd


async def test_launcher_spawns_edge_with_required_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> None:
        captured["argv"], captured["kwargs"] = argv, kwargs

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    profile = tmp_path / "profile"
    await make_edge_launcher(Path("msedge.exe"), profile, 9333)()
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--remote-debugging-port=9333" in argv and f"--user-data-dir={profile}" in argv
    assert profile.is_dir()  # profile directory is created on first launch
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict) and kwargs["stdin"] == subprocess.DEVNULL
