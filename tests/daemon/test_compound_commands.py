"""Requests made of several commands run as one ordered plan."""

from __future__ import annotations

import pytest

from agent.fastpath import plan_text
from agent.graph import success_message, with_requested_new_tab
from agent.state import ActionStep, Intent, IntentPlan
from command_queue import CommandRequest
from support import make_env


def ops(utterance: str) -> list[tuple[str, str]]:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    return [(s.op, str(s.args.get("action") or s.args.get("query") or s.args.get("key") or ""))
            for s in plan.steps]  # fmt: skip


def test_new_tab_then_play_on_youtube_music() -> None:
    plan = plan_text("Open a new tab and play The Weeknd on YouTube Music")
    assert plan is not None and plan.source == "fastpath"
    assert [s.op for s in plan.steps] == ["browser_nav", "navigate"]
    assert plan.steps[0].args == {"action": "new_tab"}
    assert plan.steps[1].args == {"query": "the weeknd", "source": "ytmusic", "play": True}
    assert plan.steps[1].expect == "video_playing"
    assert plan.intent is Intent.MEDIA_PLAY


@pytest.mark.parametrize(
    "utterance",
    [
        "open a new tab and play the weeknd on youtube music",
        "open a new tab, then play the weeknd on youtube music",
        "new tab and then play the weeknd on you tube music",
        "play the weeknd on youtube music in a new tab",
        "Hey Jarvis, open a new tab and play the weeknd on YT music please",
    ],
)
def test_phrasings_that_mean_a_new_tab_then_music(utterance: str) -> None:
    assert ops(utterance) == [("browser_nav", "new_tab"), ("navigate", "the weeknd")]


def test_names_with_and_in_them_stay_in_one_piece() -> None:
    plan = plan_text("play tom and jerry on youtube")
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].args["query"] == "tom and jerry"
    search = plan_text("search tom and jerry")
    assert search is not None and search.steps[0].args["query"] == "tom and jerry"


def test_three_parts_in_order() -> None:
    assert ops("mute, open a new tab and go fullscreen") == [
        ("media_key", "mute"),
        ("browser_nav", "new_tab"),
        ("fullscreen", ""),
    ]


def test_an_unknown_part_leaves_the_whole_request_to_the_llm() -> None:
    assert plan_text("open a new tab and do a barrel roll") is None


def test_open_a_site_in_a_new_tab() -> None:
    plan = plan_text("open youtube music in a new tab")
    assert plan is not None
    assert [s.op for s in plan.steps] == ["browser_nav", "navigate"]
    assert plan.steps[1].args["url"] == "https://music.youtube.com/"


def test_the_result_line_names_every_part() -> None:
    plan = plan_text("open a new tab and play the weeknd on youtube music")
    assert plan is not None
    assert success_message(plan) == "New tab, then playing The Weeknd"


def test_an_llm_plan_that_forgot_the_new_tab_gets_it_back() -> None:
    forgot = IntentPlan(
        intent=Intent.MEDIA_PLAY,
        confidence=0.9,
        steps=[ActionStep(op="navigate", args={"query": "x", "source": "ytmusic", "play": True})],
    )
    fixed = with_requested_new_tab(forgot, "open a new tab and play x on youtube music")
    assert [s.op for s in fixed.steps] == ["browser_nav", "navigate"]
    assert with_requested_new_tab(forgot, "play x on youtube music") is forgot
    assert with_requested_new_tab(forgot, "close the new tab and play x") is forgot
    again = with_requested_new_tab(fixed, "open a new tab and play x")
    assert len(again.steps) == 2  # never adds a second tab


async def test_the_agent_opens_a_new_tab_before_playing() -> None:
    env = make_env()
    result = await env.runner().run(
        CommandRequest(
            command_id="c1",
            idempotency_key="k1",
            device_id="d",
            kind="text",
            text="open a new tab and play the weeknd on youtube music",
        )
    )
    assert result.ok, result.detail
    new_tab = next(i for i, c in enumerate(env.browser.calls) if "new_tab" in c)
    played = next(i for i, c in enumerate(env.browser.calls) if c.startswith("search_and_play"))
    assert new_tab < played
    assert env.browser.calls[played] == "search_and_play:ytmusic:the weeknd"
    assert env.router.calls == []  # never needed the LLM
    assert result.message == "New tab, then playing The Weeknd"
