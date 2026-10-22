"""Touchpad / keyboard / manual keys from a paired phone, and the browser quick actions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from agent.fastpath import plan_button, plan_text
from contracts import (
    PHONE_TO_SERVER_TYPES,
    CmdButtonPayload,
    ErrorMsg,
    InputKey,
    InputPointer,
    InputText,
    envelope,
    parse_message,
)
from event_bus import EventBus
from remote_input import key_combo_allowed, sanitize_typed_text
from service import CommandService
from stt_engine import STTEngine
from support import AgentEnv
from timers import TimerService


@pytest.fixture
async def svc(env: AgentEnv) -> AsyncIterator[CommandService]:
    env.deps.timers = TimerService(lambda info: asyncio.sleep(0))
    service = CommandService(
        room_id="room1",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=env.deps.timers,
        stt=STTEngine(None, None),
        mode="lan",
        state_poll_s=3600,
    )
    await service.start()
    yield service
    await service.stop()


def pointer(**payload: object) -> InputPointer:
    return envelope(InputPointer, "room1", payload, from_="d1")


# ---------------------------------------------------------------- contract


def test_input_types_are_phone_messages() -> None:
    assert {"input.pointer", "input.key", "input.text"} <= PHONE_TO_SERVER_TYPES


def test_pointer_payload_is_bounded() -> None:
    assert pointer(dx=10, dy=-10, scroll=2, action="click").payload.dx == 10
    for bad in ({"dx": 5000}, {"scroll": 99}, {"action": "format_c"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            pointer(**bad)


def test_wire_roundtrip() -> None:
    raw = '{"v":1,"type":"input.key","id":"a","ts":1,"room_id":"r","from":"d","payload":{"key":"left","mods":["alt"]}}'
    msg = parse_message(raw)
    assert isinstance(msg, InputKey) and msg.payload.mods == ["alt"]


def test_new_quick_actions_exist_and_have_plans() -> None:
    for action in (
        "back", "forward", "reload", "new_tab", "next_tab", "prev_tab", "scroll_up", "scroll_down"
    ):  # fmt: skip
        CmdButtonPayload(command_id="c", idempotency_key="k", action=action)  # type: ignore[arg-type]
        (step,) = plan_button(action).steps  # type: ignore[arg-type]
        assert step.op == "browser_nav"


# ---------------------------------------------------------------- policy


@pytest.mark.parametrize(
    ("key", "mods", "ok"),
    [
        ("left", ["alt"], True),  # browser back
        ("t", ["ctrl"], True),
        ("tab", ["ctrl"], True),
        ("a", [], True),
        ("f5", [], True),
        ("esc", [], True),
        ("delete", ["ctrl", "shift"], False),  # clear browsing data
        ("esc", ["ctrl", "shift"], False),  # task manager
        ("esc", ["ctrl"], False),  # start menu
        ("delete", ["ctrl", "alt"], False),
        ("t", ["ctrl", "alt"], False),  # any Ctrl+Alt chord
        ("f4", ["alt"], False),  # not a key we offer at all
        ("win", [], False),
        ("ab", [], False),
        ("", [], False),
    ],
)
def test_key_policy(key: str, mods: list[str], ok: bool) -> None:
    assert key_combo_allowed(key, mods) is ok


def test_typed_text_is_sanitized() -> None:
    assert sanitize_typed_text("hello world") == "hello world"
    assert sanitize_typed_text("a\x00b\x07c\x1b") == "abc"
    assert sanitize_typed_text("line1\r\nline2") == "line1\nline2"
    assert sanitize_typed_text("x" * 500) == "x" * 200


# ---------------------------------------------------------------- service


async def test_pointer_moves_scrolls_and_clicks(svc: CommandService, env: AgentEnv) -> None:
    replies = await svc.handle_envelope(
        pointer(dx=12, dy=-3, scroll=2, action="double_click"), "d1"
    )
    assert replies == []
    assert env.os.pointer_log == [("move", 12, -3), ("scroll", 2), ("action", "double_click")]


async def test_zero_movement_does_nothing(svc: CommandService, env: AgentEnv) -> None:
    await svc.handle_envelope(pointer(), "d1")
    assert env.os.pointer_log == []


async def test_key_and_text_reach_the_os(svc: CommandService, env: AgentEnv) -> None:
    await svc.handle_envelope(
        envelope(InputKey, "room1", {"key": "left", "mods": ["alt"]}, from_="d1"), "d1"
    )
    await svc.handle_envelope(
        envelope(InputText, "room1", {"text": "bruno mars"}, from_="d1"), "d1"
    )
    assert env.os.pressed == [("left", ("alt",))]
    assert env.os.typed == ["bruno mars"]


async def test_denied_key_is_refused_with_an_error(svc: CommandService, env: AgentEnv) -> None:
    (err,) = await svc.handle_envelope(
        envelope(InputKey, "room1", {"key": "esc", "mods": ["ctrl", "shift"]}, from_="d1"), "d1"
    )
    assert isinstance(err, ErrorMsg) and err.payload.code == "key_not_allowed"
    assert env.os.pressed == []


async def test_remote_input_can_be_switched_off(svc: CommandService, env: AgentEnv) -> None:
    svc.remote_input_enabled = False
    (err,) = await svc.handle_envelope(pointer(dx=5), "d1")
    assert isinstance(err, ErrorMsg) and err.payload.code == "input_disabled"
    assert env.os.pointer_log == []


async def test_input_is_rate_limited_per_phone(svc: CommandService, env: AgentEnv) -> None:
    allowed = 0
    for _ in range(600):
        out = await svc.handle_envelope(pointer(dx=1), "d1")
        allowed += not out
    assert 100 <= allowed < 600  # a burst goes through, a flood does not
    # another phone is unaffected
    assert await svc.handle_envelope(pointer(dx=1), "d2") == []
    flooded = [m for m in await svc.handle_envelope(pointer(dx=1), "d1")]
    assert (
        flooded and isinstance(flooded[0], ErrorMsg) and flooded[0].payload.code == "rate_limited"
    )


# ---------------------------------------------------------------- spoken commands


@pytest.mark.parametrize(
    ("utterance", "action"),
    [
        ("new tab", "new_tab"),
        ("open a new tab", "new_tab"),
        ("go back", "back"),
        ("go back to the previous page", "back"),
        ("previous page", "back"),
        ("go forward", "forward"),
        ("refresh", "reload"),
        ("reload the page", "reload"),
        ("next tab", "next_tab"),
        ("previous tab", "prev_tab"),
        ("scroll down", "scroll_down"),
        ("scroll up a bit", "scroll_up"),
    ],
)
def test_spoken_browser_commands(utterance: str, action: str) -> None:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    (step,) = plan.steps
    assert step.op == "browser_nav" and step.args == {"action": action}


def test_close_tab_is_still_close_tab() -> None:
    (step,) = plan_text("close this tab").steps  # type: ignore[union-attr]
    assert step.op == "close_tab"


async def test_new_tab_does_not_close_anything(env: AgentEnv) -> None:
    from command_queue import CommandRequest

    result = await env.runner().run(
        CommandRequest(
            command_id="c", idempotency_key="k", device_id="d", kind="text", text="new tab"
        )
    )
    assert result.ok
    assert env.browser.calls == ["browser_nav:new_tab"]
