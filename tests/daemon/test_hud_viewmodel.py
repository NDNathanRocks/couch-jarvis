from __future__ import annotations

import asyncio
import json

import pytest

from contracts import AgentProgress, CmdResult, envelope
from event_bus import EventBus
from hud.controller import HudController
from hud.viewmodel import HudViewModel, format_remaining

CLOUD = "https://pwa.example/?room=r&secret=s"
LAN = "http://192.168.1.9:8765/?room=r&secret=s"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(clock: Clock | None = None, **kw: float) -> tuple[HudViewModel, Clock]:
    clock = clock or Clock()
    vm = HudViewModel(cloud_url=lambda: CLOUD, lan_url=lambda: LAN, clock=clock, **kw)
    return vm, clock


def device(i: str, name: str, online: bool = True) -> dict[str, object]:
    return {"device_id": i, "name": name, "online": online, "last_seen": 1.0}


class TestQrPolicy:
    def test_qr_is_shown_until_someone_pairs(self) -> None:
        vm, _ = make()
        card = vm.frame().qr
        assert card is not None and card.url == CLOUD and card.transport == "cloud"

    def test_qr_lingers_after_the_first_friend_pairs_then_hides(self) -> None:
        vm, clock = make(qr_linger_s=45)
        vm.handle_event("state.snapshot", {"devices": []})
        vm.handle_event("state.patch", {"devices": [device("a", "Ann")], "rev": 2})
        assert vm.frame().qr is not None  # visible long enough to see it worked
        clock.now += 44
        assert vm.frame().qr is not None
        clock.now += 2
        assert vm.frame().qr is None  # the TV picture is clear again

    def test_request_qr_brings_it_back_for_a_friend(self) -> None:
        vm, clock = make(qr_requested_s=90)
        vm.handle_event("state.snapshot", {"devices": [device("a", "Ann")]})
        assert vm.frame().qr is None
        vm.request_qr()
        assert vm.frame().qr is not None
        clock.now += 91
        assert vm.frame().qr is None

    def test_request_never_shortens_an_active_window(self) -> None:
        vm, clock = make()
        vm.request_qr(100)
        vm.request_qr(5)
        clock.now += 50
        assert vm.frame().qr is not None

    def test_qr_stays_hidden_when_the_phone_disconnects(self) -> None:
        vm, clock = make(qr_linger_s=10)
        vm.handle_event("state.snapshot", {"devices": []})
        vm.handle_event("state.patch", {"devices": [device("a", "Ann")], "rev": 2})
        clock.now += 11
        assert vm.frame().qr is None
        vm.handle_event("state.patch", {"devices": [device("a", "Ann", online=False)], "rev": 3})
        assert vm.frame().qr is None  # phone locked: the show must go on
        clock.now += 3600
        assert vm.frame().qr is None

    def test_toggle_shows_it_for_a_friend_until_they_connect(self) -> None:
        vm, clock = make(qr_linger_s=10)
        vm.handle_event("state.snapshot", {"devices": [device("a", "Ann")]})
        assert vm.frame().qr is None
        assert vm.toggle_qr() is True and vm.frame().qr is not None
        clock.now += 3600
        assert vm.frame().qr is not None  # stays until hidden or used
        vm.handle_event(
            "state.patch", {"devices": [device("a", "Ann"), device("b", "Ben")], "rev": 3}
        )
        assert vm.frame().qr is not None  # shown briefly so Ben sees it worked
        clock.now += 11
        assert vm.frame().qr is None

    def test_toggle_hides_it_and_hiding_before_anyone_paired_sticks(self) -> None:
        vm, _ = make()
        assert vm.frame().qr is not None
        assert vm.toggle_qr() is False and vm.frame().qr is None
        assert vm.toggle_qr() is True and vm.frame().qr is not None
        vm.hide_qr()
        assert vm.frame().qr is None

    def test_offline_phones_do_not_count_as_paired(self) -> None:
        vm, _ = make()
        vm.handle_event("state.snapshot", {"devices": [device("a", "Ann", online=False)]})
        assert vm.frame().qr is not None

    def test_internet_down_switches_the_qr_to_the_lan_address(self) -> None:
        vm, _ = make()
        vm.handle_event("state.snapshot", {"mode": "lan", "devices": []})
        card = vm.frame().qr
        assert card is not None and card.url == LAN and card.transport == "lan"
        assert "local network" in card.caption
        vm.handle_event("state.patch", {"mode": "cloud", "rev": 3})
        assert vm.frame().qr is not None and vm.frame().qr.url == CLOUD  # type: ignore[union-attr]

    def test_falls_back_when_only_one_address_is_available(self) -> None:
        clock = Clock()
        lan_only = HudViewModel(cloud_url=lambda: None, lan_url=lambda: LAN, clock=clock)
        assert lan_only.frame().qr is not None and lan_only.frame().qr.url == LAN  # type: ignore[union-attr]
        cloud_only = HudViewModel(cloud_url=lambda: CLOUD, lan_url=lambda: None, clock=clock)
        cloud_only.handle_event("state.snapshot", {"mode": "lan", "devices": []})
        assert cloud_only.frame().qr is not None and cloud_only.frame().qr.url == CLOUD  # type: ignore[union-attr]
        neither = HudViewModel(cloud_url=lambda: None, lan_url=lambda: None, clock=clock)
        assert neither.frame().qr is None


class TestToasts:
    def test_results_become_toasts_that_expire(self) -> None:
        vm, clock = make(toast_ttl_s=5)
        vm.handle_event("cmd.result", {"command_id": "c", "ok": True, "message": "Playing lofi"})
        vm.handle_event(
            "cmd.result", {"command_id": "d", "ok": False, "message": "Could not do that"}
        )
        frame = vm.frame()
        assert [(t.text, t.kind) for t in frame.toasts] == [
            ("Playing lofi", "ok"),
            ("Could not do that", "error"),
        ]
        clock.now += 5.1
        assert vm.frame().toasts == ()

    def test_only_the_newest_toasts_are_kept_and_duplicates_collapse(self) -> None:
        vm, _ = make(max_toasts=3)
        for text in ("a", "b", "a", "c", "d"):
            vm.toast(text)
        assert [t.text for t in vm.frame().toasts] == ["a", "c", "d"]

    def test_empty_messages_are_not_shown(self) -> None:
        vm, _ = make()
        vm.handle_event("cmd.result", {"command_id": "c", "ok": True, "message": "  "})
        assert vm.frame().toasts == ()

    def test_friends_joining_are_announced_but_not_at_startup(self) -> None:
        vm, _ = make()
        vm.handle_event(
            "state.snapshot", {"devices": [device("a", "Ann")]}
        )  # already there at launch
        assert vm.frame().toasts == ()
        vm.handle_event(
            "state.patch", {"devices": [device("a", "Ann"), device("b", "Bob")], "rev": 2}
        )
        assert [t.text for t in vm.frame().toasts] == ["Bob connected"]
        vm.handle_event(
            "state.patch", {"devices": [device("a", "Ann"), device("b", "Bob")], "rev": 3}
        )
        assert len(vm.frame().toasts) == 1  # no repeat for an unchanged list

    def test_a_fired_timer_shows_longer(self) -> None:
        vm, clock = make(toast_ttl_s=5)
        vm.handle_event(
            "agent.progress",
            {"command_id": "t1", "node": "timer", "status": "fired", "detail": "Sleep timer"},
        )
        clock.now += 8
        assert [t.text for t in vm.frame().toasts] == ["Timer done: Sleep timer"]
        clock.now += 3
        assert vm.frame().toasts == ()


class TestStatusTimersProgress:
    def test_status_text_and_tone(self) -> None:
        vm, _ = make()
        assert (vm.frame().status, vm.frame().tone) == ("Waiting for a phone", "warn")
        vm.handle_event("state.snapshot", {"devices": [device("a", "Ann"), device("b", "Bob")]})
        assert (vm.frame().status, vm.frame().tone) == ("Online · 2 connected", "good")
        vm.handle_event("state.patch", {"mode": "lan", "rev": 9})
        assert (vm.frame().status, vm.frame().tone) == ("Local mode · 2 connected", "warn")

    def test_timers_count_down_sorted_by_end_time(self) -> None:
        vm, clock = make()
        vm.handle_event(
            "state.snapshot",
            {
                "timers": [
                    {
                        "timer_id": "b",
                        "label": "Sleep",
                        "ends_at": clock.now + 3700,
                        "action": "pause",
                    },
                    {
                        "timer_id": "a",
                        "label": "Pasta",
                        "ends_at": clock.now + 65,
                        "action": "notify",
                    },
                ]
            },
        )
        assert [(t.label, t.remaining) for t in vm.frame().timers] == [
            ("Pasta", "1:05"),
            ("Sleep", "1:01:40"),
        ]
        clock.now += 61
        assert vm.frame().timers[0].remaining == "0:04"
        clock.now += 100
        assert vm.frame().timers[0].remaining == "0:00"  # never negative

    @pytest.mark.parametrize(
        ("seconds", "label"),
        [
            (0, "0:00"),
            (59.2, "1:00"),
            (60, "1:00"),
            (3599, "59:59"),
            (3600, "1:00:00"),
            (-5, "0:00"),
        ],
    )
    def test_format_remaining(self, seconds: float, label: str) -> None:
        assert format_remaining(seconds) == label

    def test_progress_shows_while_a_command_runs_and_clears_on_result(self) -> None:
        vm, _ = make()
        vm.handle_event(
            "agent.progress", {"command_id": "c", "node": "intent_router", "status": "start"}
        )
        assert vm.frame().progress == "Thinking…"
        vm.handle_event(
            "agent.progress", {"command_id": "c", "node": "vision_fallback", "status": "start"}
        )
        assert vm.frame().progress == "Looking at the screen…"
        vm.handle_event(
            "agent.progress", {"command_id": "c", "node": "vision_fallback", "status": "done"}
        )
        assert vm.frame().progress == "Looking at the screen…"  # "done" does not change the label
        vm.handle_event("cmd.result", {"command_id": "c", "ok": True, "message": "Done"})
        assert vm.frame().progress is None

    def test_unknown_and_malformed_events_are_ignored(self) -> None:
        vm, _ = make()
        before = vm.frame()
        vm.handle_event("telemetry.span", {"x": 1})
        vm.handle_event("state.patch", {"mode": "bogus", "rev": 1})
        assert vm.frame() == before


class TestController:
    async def test_bus_events_drive_the_view_model(self) -> None:
        from support import make_env

        env = make_env()
        from service import CommandService
        from timers import TimerService

        bus = EventBus()
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
        vm, _ = make()
        changes: list[int] = []
        task = asyncio.create_task(HudController(service, vm, lambda: changes.append(1)).run())
        await asyncio.sleep(0.02)
        assert changes  # seeded immediately
        bus.publish(
            envelope(CmdResult, "r", {"command_id": "c", "ok": True, "message": "Playing lofi"})
        )
        bus.publish(
            envelope(
                AgentProgress,
                "r",
                {"command_id": "d", "node": "stt", "status": "start", "detail": ""},
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        frame = vm.frame()
        assert [t.text for t in frame.toasts] == ["Playing lofi"] and frame.progress == "Listening…"
        assert json.loads(json.dumps(frame.status))  # plain string, safe to log


class TestStatusBadgeFades:
    def test_visible_at_first_then_gone_after_ten_quiet_seconds(self) -> None:
        vm, clock = make()
        assert vm.frame().status_opacity == 1.0
        clock.now += 9.9
        assert vm.frame().status_opacity == 1.0
        clock.now += 1.1  # 11 s of quiet: halfway through the 2 s fade
        assert 0 < vm.frame().status_opacity < 1
        clock.now += 5
        assert vm.frame().status_opacity == 0.0

    def test_any_phone_activity_brings_it_back(self) -> None:
        vm, clock = make()
        clock.now += 30
        assert vm.frame().status_opacity == 0.0
        vm.touch()
        assert vm.frame().status_opacity == 1.0

    def test_commands_and_progress_wake_it(self) -> None:
        vm, clock = make()
        clock.now += 30
        vm.frame()
        vm.handle_event("cmd.ack", {"command_id": "c1"})
        assert vm.frame().status_opacity == 1.0
        clock.now += 30
        vm.handle_event(
            "agent.progress", {"command_id": "c2", "node": "intent_router", "status": "start"}
        )
        assert vm.frame().status_opacity == 1.0  # shown while the assistant is working

    def test_a_phone_joining_or_leaving_shows_it_again(self) -> None:
        vm, clock = make()
        clock.now += 30
        assert vm.frame().status_opacity == 0.0
        device = {"device_id": "d1", "name": "Pixel", "online": True}
        vm.handle_event("state.patch", {"devices": [device]})
        assert vm.frame().status_opacity == 1.0

    def test_zero_means_always_on(self) -> None:
        vm, clock = make(badge_hide_s=0)
        clock.now += 3600
        assert vm.frame().status_opacity == 1.0
