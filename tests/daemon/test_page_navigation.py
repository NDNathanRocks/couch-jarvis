"""Acting on the page the user is looking at: episode/result picks, search-only, fullscreen wake."""

from __future__ import annotations

import json

import pytest

from agent.fastpath import plan_text
from command_queue import CommandRequest
from executors.page_items import Candidate, pick_item
from support import AgentEnv

STREAMING = "https://heartivelovestv.pages.dev/#/tv/1418"


def text(utterance: str) -> CommandRequest:
    return CommandRequest(
        command_id="c-1", idempotency_key="k", device_id="d1", kind="text", text=utterance
    )


def cands(*texts: str) -> list[Candidate]:
    return [Candidate(index=i, text=t) for i, t in enumerate(texts)]


# ---------------------------------------------------------------- fast path


@pytest.mark.parametrize(
    ("utterance", "args"),
    [
        ("play episode 5", {"episode": 5}),
        ("episode 12", {"episode": 12}),
        ("watch episode number 3", {"episode": 3}),
        ("hey jarvis play ep 7 please", {"episode": 7}),
        ("play the third episode", {"episode": 3}),
        ("open the 10th episode", {"episode": 10}),
        ("play the second one", {"result": 2}),
        ("click the first result", {"result": 1}),
        ("open the 4th video", {"result": 4}),
        ("play number 2", {"result": 2}),
    ],
)
def test_page_picks_never_leave_the_page_or_wait_on_the_llm(
    utterance: str, args: dict[str, int]
) -> None:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    assert plan.source == "fastpath"
    (step,) = plan.steps
    assert step.op == "click_item" and step.args == args


@pytest.mark.parametrize(
    ("utterance", "query", "source"),
    [
        ("search for bruno mars", "bruno mars", "youtube"),
        ("search bruno mars on youtube", "bruno mars", "youtube"),
        ("search youtube for bruno mars", "bruno mars", "youtube"),
        ("look up bruno mars", "bruno mars", "youtube"),
        ("search for uptown funk on youtube music", "uptown funk", "ytmusic"),
    ],
)
def test_search_only_shows_results_and_waits(utterance: str, query: str, source: str) -> None:
    plan = plan_text(utterance)
    assert plan is not None, utterance
    (step,) = plan.steps
    assert step.op == "navigate"
    assert step.args == {"query": query, "source": source, "play": False}


def test_plain_play_still_goes_to_the_llm() -> None:
    assert plan_text("play bruno mars") is None
    assert plan_text("play lofi hip hop on youtube") is None


# ---------------------------------------------------------------- picking


def test_episode_prefers_the_row_that_starts_with_the_number() -> None:
    items = cands(
        "Continue S1 · E5", "1\nPilot", "5\nThe Hamburger Postulate", "15\nThe Hofstadter"
    )
    assert pick_item(items, episode=5) == 2


@pytest.mark.parametrize(
    "row",
    ["E5 · The Hamburger Postulate", "Episode 5: The Hamburger Postulate", "05. The Hamburger"],
)
def test_episode_number_formats(row: str) -> None:
    assert pick_item(cands("Home", "Episode 4: x", row, "Episode 6: y"), episode=5) == 2


def test_episode_not_found() -> None:
    assert pick_item(cands("Home", "1\nPilot"), episode=9) is None


def test_text_match_and_index_match() -> None:
    items = cands("Home", "1\nPilot", "2\nThe Big Bran Hypothesis")
    assert pick_item(items, text="big bran hypothesis") == 2
    assert pick_item(items, index=1) == 1
    assert pick_item(items, index=9) is None


def test_result_picks_the_nth_content_row() -> None:
    items = [
        Candidate(0, "Home"),
        Candidate(1, "Bruno Mars - Uptown Funk", group="result"),
        Candidate(2, "Bruno Mars - 24K Magic", group="result"),
    ]
    assert pick_item(items, result=2) == 2


# ---------------------------------------------------------------- graph


async def test_play_episode_clicks_on_a_non_youtube_site(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"url": STREAMING, "title": "Big Bang"})
    result = await env.runner().run(text("play episode 5"))
    assert result.ok, result.message
    assert env.router.calls == []
    assert env.browser.calls == ["click_item:episode=5"]


async def test_click_item_is_refused_off_http_pages(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"url": "edge://settings"})
    result = await env.runner().run(text("play episode 5"))
    assert not result.ok
    assert env.browser.calls == []


async def test_router_sees_the_page_items(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"url": STREAMING, "title": "Big Bang"})
    env.browser.items = ["Home", "1\nPilot", "2\nThe Big Bran Hypothesis"]
    env.router.replies = [
        json.dumps(
            {
                "intent": "media.play",
                "confidence": 0.9,
                "steps": [{"op": "click_item", "args": {"text": "Pilot"}}],
            }
        )
    ]
    result = await env.runner().run(text("put on the pilot"))
    assert result.ok, result.message
    prompt = env.router.calls[0][1]
    assert "<untrusted_page_items>" in prompt and "Pilot" in prompt
    assert "site=other" in prompt and "heartivelovestv" not in prompt  # classification, no host
    assert env.browser.calls == ["click_item:text=Pilot"]


async def test_llm_cannot_navigate_away_from_a_site_unless_asked(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"url": STREAMING, "title": "Big Bang"})
    env.router.replies = [
        json.dumps(
            {
                "intent": "media.play",
                "confidence": 0.9,
                "steps": [
                    {"op": "navigate", "args": {"query": "big bang episode", "source": "youtube"}}
                ],
            }
        )
    ]
    result = await env.runner().run(text("play the one with the cat"))
    assert not result.ok
    assert not any(c.startswith("search") for c in env.browser.calls)


async def test_search_only_does_not_play(env: AgentEnv) -> None:
    result = await env.runner().run(text("search for bruno mars"))
    assert result.ok, result.message
    assert env.browser.calls == ["search:youtube:bruno mars"]
    assert env.browser.snap.is_playing is None


# ---------------------------------------------------------------- fullscreen wake


async def test_fullscreen_wakes_the_controls_before_every_tier(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "selector_timeout"
    env.vlm.replies = ['{"found": false}']
    await env.runner().run(text("fullscreen"))
    # each attempt wakes once for the DOM/JS tiers and once more before the VLM screenshot
    assert env.browser.wakes >= 2 and env.browser.wakes % 2 == 0
