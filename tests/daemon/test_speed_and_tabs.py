"""Playback speed, leaving fullscreen and duplicating a tab: by voice and by button."""

from __future__ import annotations

import pytest

from agent.fastpath import plan_button, plan_text
from command_queue import CommandRequest
from support import make_env


def req(text: str) -> CommandRequest:
    return CommandRequest(
        command_id="c", idempotency_key="k", device_id="d", kind="voice", text=text
    )


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("change the speed of the player to 1.25 times speed", {"rate": 1.25}),
        ("set playback speed to 0.75", {"rate": 0.75}),
        ("play at 2x", {"rate": 2.0}),
        ("1.5x speed", {"rate": 1.5}),
        ("speed 1 point 5", {"rate": 1.5}),
        ("normal speed", {"rate": 1.0}),
        ("half speed", {"rate": 0.5}),
        ("reset the speed", {"rate": 1.0}),
        ("speed it up", {"delta": 0.25}),
        ("slow down", {"delta": -0.25}),
    ],
)
def test_spoken_speeds(said: str, args: dict[str, float]) -> None:
    plan = plan_text(said)
    assert plan is not None and plan.source == "fastpath"
    assert [(s.op, s.args) for s in plan.steps] == [("speed", args)]


def test_impossible_speeds_go_to_the_ai() -> None:
    plan = plan_text("play at 9x")
    assert plan is None or all(s.op != "speed" for s in plan.steps)


@pytest.mark.parametrize(
    "said", ["exit fullscreen", "unfullscreen", "un-fullscreen", "leave full screen mode"]
)
def test_leaving_fullscreen(said: str) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("browser_nav", {"action": "exit_fullscreen"})]


@pytest.mark.parametrize("said", ["duplicate this tab", "duplicate tab", "clone the current tab"])
def test_duplicating_a_tab(said: str) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("browser_nav", {"action": "duplicate_tab"})]


async def test_speed_reaches_the_browser() -> None:
    env = make_env()
    result = await env.runner().run(req("change the speed of the player to 1.25 times speed"))
    assert result.ok and "1.25" in result.message
    assert env.browser.speed == 1.25


async def test_leaving_fullscreen_needs_no_remote_input() -> None:
    env = make_env()
    env.deps.remote_input = False  # it used to be an Esc key press, refused with keys off
    result = await env.runner().run(req("exit fullscreen"))
    assert result.ok and "browser_nav:exit_fullscreen" in env.browser.calls


def test_buttons() -> None:
    assert plan_button("exit_fullscreen").steps[0].args == {"action": "exit_fullscreen"}
    assert plan_button("duplicate_tab").steps[0].args == {"action": "duplicate_tab"}
