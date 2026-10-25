"""Spoken key presses ("press F11") and the function keys a phone may send."""

from __future__ import annotations

import json
import re

import pytest

from agent.fastpath import plan_text
from command_queue import CommandRequest
from contracts import REMOTE_KEY_PATTERN
from remote_input import key_combo_allowed
from support import make_env


def req(text: str) -> CommandRequest:
    return CommandRequest(
        command_id="c", idempotency_key="k", device_id="d", kind="voice", text=text
    )


@pytest.mark.parametrize(
    ("said", "key", "mods"),
    [
        ("press F11", "f11", None),
        ("press F 11", "f11", None),
        ("hit escape", "esc", None),
        ("press the enter key", "enter", None),
        ("press control L", "l", "ctrl"),
        ("press alt left arrow", "left", "alt"),
        ("press ctrl and shift tab", "tab", "ctrl+shift"),
        ("tap space bar", "space", None),
    ],
)
def test_spoken_key_presses(said: str, key: str, mods: str | None) -> None:
    plan = plan_text(said)
    assert plan is not None and plan.source == "fastpath"
    step = plan.steps[0]
    assert step.op == "press_key" and step.args["key"] == key and step.args.get("mods") == mods


def test_other_sentences_are_not_key_presses() -> None:
    for said in ("press play", "hit it", "press escape and play something"):
        plan = plan_text(said)
        assert plan is None or plan.steps[0].op != "press_key"


def test_function_keys_f1_to_f12_are_allowed_and_alt_f4_is_not() -> None:
    pattern = re.compile(REMOTE_KEY_PATTERN)
    assert all(pattern.fullmatch(f"f{n}") for n in range(1, 13))
    assert not pattern.fullmatch("f13") and not pattern.fullmatch("f0")
    assert key_combo_allowed("f11", ())
    assert not key_combo_allowed("f4", ("alt",))
    assert key_combo_allowed("f4", ())  # F4 alone is harmless


async def test_pressing_f11_reaches_the_keyboard() -> None:
    env = make_env()
    result = await env.runner().run(req("press F11"))
    assert result.ok and "F11" in result.message
    assert env.os.pressed == [("f11", ())]


async def test_spoken_keys_follow_the_remote_input_switch() -> None:
    env = make_env()
    env.deps.remote_input = False
    result = await env.runner(max_attempts=0).run(req("press F11"))
    assert not result.ok and not env.os.pressed


async def test_a_blocked_chord_is_refused() -> None:
    env = make_env()
    result = await env.runner(max_attempts=0).run(req("press alt f4"))
    assert not env.os.pressed
    assert not result.ok or "F4" not in result.message


async def test_the_model_cannot_use_press_key() -> None:
    env = make_env()
    plan = {
        "intent": "system",
        "confidence": 0.9,
        "slots": {},
        "source": "llm",
        "steps": [{"op": "press_key", "args": {"key": "w", "mods": "ctrl"}}],
    }
    env.router.replies += [json.dumps(plan), json.dumps(plan)]
    result = await env.runner(max_attempts=0).run(req("close everything for me please"))
    assert not result.ok and not env.os.pressed


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("qr_control", {"action": "reset"}),
        ("bookmark", {"action": "remove", "name": "netflix"}),
        ("browser_pref", {"adblock": False}),
        ("type", {"text": "hello"}),
    ],
)
async def test_the_model_cannot_use_ops_reserved_for_the_user(
    op: str, args: dict[str, object]
) -> None:
    """A web page that steers the model must not rotate the pairing secret, delete saved pages or
    switch the ad blocker off: those exist only as the user's own spoken commands."""
    env = make_env()
    plan = {
        "intent": "system",
        "confidence": 0.9,
        "slots": {},
        "source": "llm",
        "steps": [{"op": op, "args": args}],
    }
    env.router.replies += [json.dumps(plan), json.dumps(plan)]
    result = await env.runner(max_attempts=0).run(req("tidy things up for me please"))
    assert not result.ok
