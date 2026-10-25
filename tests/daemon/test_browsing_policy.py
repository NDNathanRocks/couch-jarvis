"""Ad blocking switch, and which tabs a page may open."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.fastpath import plan_text
from agent.graph import without_unrequested_clicks
from agent.state import ActionStep, Intent, IntentPlan
from browser_prefs import BrowserPrefs
from command_queue import CommandRequest
from executors.edge_cdp import EdgeCDP
from support import make_env


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.fronted = False
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, fn: Any) -> None:
        self.handlers[event] = fn

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    async def bring_to_front(self) -> None:
        self.fronted = True

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None


def edge(adblock: bool) -> EdgeCDP:
    prefs = BrowserPrefs(None)
    prefs.apply({"adblock": adblock})
    return EdgeCDP("http://127.0.0.1:9222", prefs=prefs)


async def test_popup_from_search_results_opens_and_becomes_the_page() -> None:
    cdp = edge(True)
    opener, popup = FakePage("https://www.google.com/search?q=cats"), FakePage("https://cats.org/")
    await cdp._on_popup(opener, popup)
    assert not popup.closed and popup.fronted and cdp._page_ref is popup


async def test_third_party_popup_is_closed_while_blocking() -> None:
    cdp = edge(True)
    opener, popup = FakePage("https://stream.example.com/w/1"), FakePage("https://ads.bad.xyz/x")
    await cdp._on_popup(opener, popup)
    assert popup.closed and cdp._page_ref is None


async def test_same_site_tab_is_allowed_while_blocking() -> None:
    cdp = edge(True)
    opener, popup = (
        FakePage("https://stream.example.com/w/1"),
        FakePage("https://www.example.com/p"),
    )
    await cdp._on_popup(opener, popup)
    assert not popup.closed


async def test_anything_opens_when_blocking_is_off() -> None:
    cdp = edge(False)
    opener, popup = FakePage("https://stream.example.com/w/1"), FakePage("https://ads.bad.xyz/x")
    await cdp._on_popup(opener, popup)
    assert not popup.closed and cdp._page_ref is popup


def test_prefs_persist_and_default_to_blocking(tmp_path: Path) -> None:
    path = tmp_path / "browser.json"
    seen: list[bool] = []
    prefs = BrowserPrefs(path, on_change=lambda s: seen.append(s.adblock))
    assert prefs.adblock is True
    assert prefs.apply({"adblock": False}) == "Pop-up protection off"
    assert BrowserPrefs(path).adblock is False and seen == [False]
    path.write_text("junk", encoding="utf-8")
    assert BrowserPrefs(path).adblock is True


@pytest.mark.parametrize(
    ("said", "adblock"),
    [
        ("turn off ad blocking", False),
        ("disable the popup blocker", False),
        ("allow popups", False),
        ("turn on ad blocking", True),
        ("block ads", True),
        ("turn off pop-up protection", False),
        ("turn on the popup protection", True),
    ],
)
def test_spoken_switch(said: str, adblock: bool) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("browser_pref", {"adblock": adblock})]


async def test_the_agent_flips_the_switch() -> None:
    env = make_env()
    env.deps.prefs = BrowserPrefs(None)
    req = CommandRequest(
        command_id="c", idempotency_key="k", device_id="d", kind="text", text="turn off ad blocking"
    )
    result = await env.runner().run(req)
    assert result.ok and result.message == "Pop-up protection off"
    assert env.deps.prefs.adblock is False


def _plan(*steps: ActionStep) -> IntentPlan:
    return IntentPlan(intent=Intent.MEDIA_PLAY, confidence=0.9, steps=list(steps))


def test_search_never_clicks_a_result_unless_asked() -> None:
    sneaky = _plan(
        ActionStep(op="page_search", args={"query": "cats"}),
        ActionStep(op="click_item", args={"result": 1}),
    )
    assert [s.op for s in without_unrequested_clicks(sneaky, "search for cats").steps] == [
        "page_search"
    ]
    kept = without_unrequested_clicks(sneaky, "search for cats and open the first one")
    assert [s.op for s in kept.steps] == ["page_search", "click_item"]


def test_search_never_autoplays() -> None:
    plan = _plan(ActionStep(op="navigate", args={"query": "x", "source": "youtube", "play": True}))
    fixed = without_unrequested_clicks(plan, "search x on youtube")
    assert fixed.steps[0].args["play"] is False
    assert without_unrequested_clicks(plan, "play x on youtube") is plan


@pytest.mark.parametrize(
    ("said", "query", "engine"),
    [
        ("search for cats on google", "cats", "google"),
        ("search cats on bing", "cats", "bing"),
        ("google best pizza near me", "best pizza near me", "google"),
        ("search google for cats", "cats", "google"),
        ("look up cats on edge", "cats", "bing"),
    ],
)
def test_named_search_engines_open_a_results_tab(said: str, query: str, engine: str) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [
        ("web_search", {"query": query, "engine": engine})
    ]


@pytest.mark.parametrize(
    ("said", "text"),
    [
        ("click on season 1", "season 1"),
        ("click on the season 1 on the screen", "season 1"),
        ("press play", "play"),
        ("tap the button that says Next Episode", "next episode"),
        ("select the subscribe button", "subscribe"),
        ('click on "Watch now"', "watch now"),
    ],
)
def test_click_on_what_it_says(said: str, text: str) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("click_text", {"text": text})]


@pytest.mark.parametrize("said", ["click it", "click on the first one", "click episode 5", "click"])
def test_clicks_that_are_not_by_text(said: str) -> None:
    plan = plan_text(said)
    assert plan is None or all(s.op != "click_text" for s in plan.steps)


async def test_click_text_uses_the_dom_then_the_screen() -> None:
    env = make_env()
    env.browser.visible_text.add("season 1")
    ok = await env.runner().run(
        CommandRequest(
            command_id="a",
            idempotency_key="a",
            device_id="d",
            kind="text",
            text="click on season 1",
        )
    )
    assert ok.ok and env.browser.calls[-1] == "click_text:season 1"


async def test_click_text_falls_back_to_the_screen_on_any_site() -> None:
    import json

    env = make_env()
    env.browser.snap = env.browser.snap.model_copy(update={"url": "https://example.com/"})
    env.vlm.replies = [
        json.dumps(
            {"found": True, "mark_id": 5, "bbox_norm": [0.4, 0.4, 0.6, 0.6], "rationale": "x"}
        )
    ]
    result = await env.runner().run(
        CommandRequest(
            command_id="b",
            idempotency_key="b",
            device_id="d",
            kind="text",
            text="click on season 1",
        )
    )
    assert result.ok and result.tier_used == "vision"
    assert env.os.clicks == [(960, 540)]
    assert "season 1" in env.vlm.calls[-1][1]
