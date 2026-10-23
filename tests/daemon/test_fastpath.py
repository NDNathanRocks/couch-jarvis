from __future__ import annotations

from typing import get_args

import pytest

from agent.fastpath import normalize, plan_button, plan_text
from agent.state import Intent
from contracts import QuickAction

CASES: list[tuple[str, Intent, str, dict[str, object]]] = [
    ("pause", Intent.MEDIA_CONTROL, "media_key", {"key": "pause"}),
    ("Pause the video", Intent.MEDIA_CONTROL, "media_key", {"key": "pause"}),
    ("hey jarvis, please stop the music!", Intent.MEDIA_CONTROL, "media_key", {"key": "pause"}),
    ("resume", Intent.MEDIA_CONTROL, "media_key", {"key": "play"}),
    ("play", Intent.MEDIA_CONTROL, "media_key", {"key": "play"}),
    ("next song", Intent.MEDIA_CONTROL, "media_key", {"key": "next"}),
    ("skip", Intent.MEDIA_CONTROL, "media_key", {"key": "next"}),
    ("previous track", Intent.MEDIA_CONTROL, "media_key", {"key": "prev"}),
    ("volume to 30", Intent.VOLUME, "volume_set", {"level": 30}),
    ("set the volume to 45 percent", Intent.VOLUME, "volume_set", {"level": 45}),
    ("volume at 250", Intent.VOLUME, "volume_set", {"level": 100}),
    ("louder", Intent.VOLUME, "media_key", {"key": "vol_up"}),
    ("turn it down", Intent.VOLUME, "media_key", {"key": "vol_down"}),
    ("mute", Intent.VOLUME, "media_key", {"key": "mute", "state": "on"}),
    ("unmute", Intent.VOLUME, "media_key", {"key": "mute", "state": "off"}),
    ("fullscreen", Intent.FULLSCREEN, "fullscreen", {}),
    ("make it full screen", Intent.FULLSCREEN, "fullscreen", {}),
    ("skip the ad", Intent.MEDIA_CONTROL, "close_ad", {}),
    ("close this tab", Intent.TAB, "close_tab", {}),
    ("skip ahead", Intent.MEDIA_CONTROL, "seek", {"delta_s": 10}),
    ("rewind 30 seconds", Intent.MEDIA_CONTROL, "seek", {"delta_s": -30}),
    ("turn off the tv", Intent.SYSTEM, "sleep_display", {}),
    ("show the qr code", Intent.SYSTEM, "show_qr", {}),
    ("show pairing QR code", Intent.SYSTEM, "show_qr", {}),
    ("pair a friend", Intent.SYSTEM, "show_qr", {}),
    ("add another phone", Intent.SYSTEM, "show_qr", {}),
]


@pytest.mark.parametrize(("utterance", "intent", "op", "args"), CASES)
def test_recognized_commands_skip_the_llm(
    utterance: str, intent: Intent, op: str, args: dict[str, object]
) -> None:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    assert plan.source == "fastpath" and plan.confidence == 1.0
    assert plan.intent is intent
    assert plan.steps[0].op == op
    for key, value in args.items():
        assert plan.steps[0].args[key] == value  # type: ignore[index]


@pytest.mark.parametrize(
    "utterance",
    [
        "play the new taylor swift album",
        "what is the weather",
        "",
        "volume",
        "pause the world",
        "show me the weather",
        "pair of shoes",
    ],
)
def test_open_ended_requests_fall_through_to_the_llm(utterance: str) -> None:
    assert plan_text(utterance) is None


def test_timer_phrases() -> None:
    plan = plan_text("sleep timer 30 minutes")
    assert plan is not None and plan.intent is Intent.TIMER
    assert plan.steps[0].args == {"minutes": 30.0, "label": "30 minutes", "action": "pause"}
    hour = plan_text("set a timer for 2 hours")
    assert hour is not None and hour.steps[0].args["minutes"] == 120.0
    assert hour.steps[0].args["action"] == "notify"
    secs = plan_text("timer 90 seconds")
    assert secs is not None and secs.steps[0].args["minutes"] == 1.5


def test_pause_expects_paused_and_play_expects_playing() -> None:
    assert plan_text("pause").steps[0].expect == "paused"  # type: ignore[union-attr]
    assert plan_text("play").steps[0].expect == "video_playing"  # type: ignore[union-attr]


def test_normalize() -> None:
    assert normalize("  Hey Jarvis, please Pause!!  ") == "pause"


@pytest.mark.parametrize("action", get_args(QuickAction))
def test_every_quick_action_has_a_plan(action: str) -> None:
    plan = plan_button(action)  # type: ignore[arg-type]
    assert plan.source == "fastpath" and len(plan.steps) == 1


def test_panic_is_an_idempotent_pause_not_a_toggle() -> None:
    step = plan_button("panic").steps[0]
    assert step.args["key"] == "pause" and step.expect == "paused"


def test_naming_the_site_is_enough_to_skip_the_llm() -> None:
    plan = plan_text("play lofi hip hop on youtube")
    assert plan is not None and plan.source == "fastpath"
    assert plan.steps[0].args == {"query": "lofi hip hop", "source": "youtube", "play": True}
