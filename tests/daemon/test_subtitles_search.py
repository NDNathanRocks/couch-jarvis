"""Subtitles (default English, any player) and "search" that uses the page's own search bar."""

from __future__ import annotations

import json

import pytest

from agent.fastpath import plan_button, plan_text
from command_queue import CommandRequest
from contracts import CmdButtonPayload, CmdResultPayload
from support import AgentEnv

STREAMING = "https://heartivelovestv.pages.dev/#/tv/1418"


def text(utterance: str) -> CommandRequest:
    return CommandRequest(
        command_id="c-1", idempotency_key="k", device_id="d1", kind="text", text=utterance
    )


def on_streaming_site(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"url": STREAMING, "title": "Big Bang"})


# ---------------------------------------------------------------- spoken phrases: subtitles


@pytest.mark.parametrize(
    ("utterance", "state", "lang"),
    [
        ("turn on subtitles", "on", None),
        ("subtitles on", "on", None),
        ("turn on the captions", "on", None),
        ("enable closed captions", "on", None),
        ("show subtitles", "on", None),
        ("turn off subtitles", "off", None),
        ("subtitles off", "off", None),
        ("hide the captions", "off", None),
        ("subtitles", "toggle", None),
        ("cc", "toggle", None),
        ("english subtitles", "on", "en"),
        ("subtitles in spanish", "on", "es"),
        ("turn on french subtitles", "on", "fr"),
        ("captions in german please", "on", "de"),
    ],
)
def test_subtitle_phrases(utterance: str, state: str, lang: str | None) -> None:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    (step,) = plan.steps
    assert step.op == "subtitles"
    assert step.args["state"] == state
    assert step.args.get("lang") == lang


def test_the_subtitles_button_is_a_toggle_and_accepted_by_the_contract() -> None:
    CmdButtonPayload(command_id="c", idempotency_key="k", action="subtitles")
    (step,) = plan_button("subtitles").steps
    assert step.op == "subtitles" and step.args["state"] == "toggle"


# ---------------------------------------------------------------- spoken phrases: search


@pytest.mark.parametrize(
    "utterance",
    [
        "search big bang theory",
        "search for bruno mars",
        "look up the office",
        "hey jarvis search cats",
    ],
)
def test_plain_search_uses_the_page_search_bar(utterance: str) -> None:
    plan = plan_text(utterance)
    assert plan is not None
    (step,) = plan.steps
    assert step.op == "page_search" and step.args["query"]


def test_search_query_is_preserved() -> None:
    (step,) = plan_text("search big bang theory").steps  # type: ignore[union-attr]
    assert step.args == {"query": "big bang theory"}


@pytest.mark.parametrize(
    ("utterance", "source"),
    [
        ("search bruno mars on youtube", "youtube"),
        ("search youtube for bruno mars", "youtube"),
        ("search uptown funk on youtube music", "ytmusic"),
    ],
)
def test_naming_youtube_still_searches_youtube(utterance: str, source: str) -> None:
    (step,) = plan_text(utterance).steps  # type: ignore[union-attr]
    assert step.op == "navigate" and step.args["source"] == source and step.args["play"] is False


def test_the_suggested_follow_up_opens_a_new_tab_then_searches() -> None:
    plan = plan_text("search for big bang theory on youtube in a new tab")
    assert plan is not None
    assert [s.op for s in plan.steps] == ["browser_nav", "navigate"]
    assert plan.steps[0].args == {"action": "new_tab"}
    assert plan.steps[1].args["query"] == "big bang theory" and plan.steps[1].args["play"] is False


# ---------------------------------------------------------------- graph: page search


async def test_search_types_into_the_search_bar_on_the_current_site(env: AgentEnv) -> None:
    on_streaming_site(env)
    result = await env.runner().run(text("search big bang theory"))
    assert result.ok, result.message
    assert env.browser.calls == ["page_search:big bang theory"]  # no navigation away
    assert result.suggest is None


async def test_no_search_bar_asks_instead_of_guessing(env: AgentEnv) -> None:
    on_streaming_site(env)
    env.browser.search_bar = False
    result = await env.runner().run(text("search big bang theory"))
    assert not result.ok
    assert "search bar" in result.message.lower()
    assert result.suggest is not None
    assert result.suggest.text == "search for big bang theory on youtube in a new tab"
    assert "new tab" in result.suggest.label.lower()
    assert env.browser.calls == ["page_search:big bang theory"]  # it did not open anything
    assert "suggest" in CmdResultPayload.model_fields


async def test_accepting_the_suggestion_opens_a_tab_and_searches_youtube(env: AgentEnv) -> None:
    on_streaming_site(env)
    result = await env.runner().run(text("search for big bang theory on youtube in a new tab"))
    assert result.ok, result.message
    assert env.browser.calls == ["browser_nav:new_tab", "search:youtube:big bang theory"]


# ---------------------------------------------------------------- graph: subtitles


async def test_subtitles_default_to_english(env: AgentEnv) -> None:
    on_streaming_site(env)
    result = await env.runner().run(text("turn on subtitles"))
    assert result.ok, result.message
    assert env.browser.calls == ["subtitles:on:en"]
    assert "english" in result.message.lower() or "subtitles" in result.message.lower()


async def test_an_explicit_language_wins(env: AgentEnv) -> None:
    result = await env.runner().run(text("subtitles in spanish"))
    assert result.ok
    assert env.browser.calls == ["subtitles:on:es"]


async def test_the_configured_default_language_is_used(env: AgentEnv) -> None:
    env.deps.subtitle_lang = "fr"
    await env.runner().run(text("subtitles on"))
    assert env.browser.calls == ["subtitles:on:fr"]


async def test_turning_subtitles_off(env: AgentEnv) -> None:
    result = await env.runner().run(text("turn off subtitles"))
    assert result.ok and env.browser.calls == ["subtitles:off:en"]


async def test_vision_finds_the_cc_button_when_the_dom_cannot(env: AgentEnv) -> None:
    on_streaming_site(env)
    env.browser.subtitle_dom_fail = True
    hit = {"found": True, "mark_id": 5, "bbox_norm": [0.8, 0.9, 0.84, 0.95], "rationale": "CC"}
    menu = {"found": True, "mark_id": 9, "bbox_norm": [0.6, 0.4, 0.8, 0.45], "rationale": "English"}
    env.vlm.replies = [json.dumps(hit), json.dumps(menu)]
    result = await env.runner().run(text("turn on subtitles"))
    assert result.ok, result.message
    assert result.tier_used == "vision"
    assert env.vlm.image_calls == 2  # the CC button, then the English entry in its menu
    assert len(env.os.clicks) == 2


async def test_no_subtitles_anywhere_ends_with_a_clear_message(env: AgentEnv) -> None:
    env.browser.subtitle_dom_fail = True
    env.vlm.replies = ['{"found": false}']
    result = await env.runner().run(text("turn on subtitles"))
    assert not result.ok
    assert "subtitle" in result.message.lower()
    assert env.vlm.image_calls == 1  # no retry loop over a missing control
