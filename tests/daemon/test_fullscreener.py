from __future__ import annotations

import json

from executors.base import ActionResult
from support import AgentEnv

HIT = json.dumps({"found": True, "bbox_norm": [0.9, 0.9, 1.0, 1.0], "mark_id": 96})


async def test_tier1_dom_success_short_circuits(env: AgentEnv) -> None:
    result = await env.fullscreener.run("fullscreen")
    assert result.ok and result.tier == "dom"
    assert env.browser.calls == ["fullscreen_dom", "settle_fullscreen"]
    assert env.vlm.image_calls == 0


async def test_tier2_js_runs_only_after_dom_fails(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "overlay"
    env.browser.js_ok = True
    result = await env.fullscreener.run("fullscreen")
    assert result.ok and result.tier == "js"
    assert env.browser.calls == ["fullscreen_dom", "fullscreen_js", "settle_fullscreen"]
    assert env.vlm.image_calls == 0 and env.os.clicks == []


async def test_tier3_vision_runs_last_in_order(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "iframe_cross_origin"
    env.vlm.replies = [HIT]
    result = await env.fullscreener.run("fullscreen")
    assert result.ok and result.tier == "vision"
    assert env.browser.calls == ["fullscreen_dom", "fullscreen_js", "settle_fullscreen"]
    assert env.vlm.image_calls == 1 and len(env.os.clicks) == 1
    tiers = [
        r["attributes"]["cj.tier"] for r in env.telemetry.recent() if r["name"] == "executor.step"
    ]
    assert tiers == ["dom", "js", "vision"]


async def test_failed_tiers_report_the_root_cause(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "overlay"
    result = await env.fullscreener.dom_js_tiers("fullscreen")
    assert not result.ok and result.fallback_reason == "overlay"


async def test_already_satisfied_does_nothing(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"is_fullscreen": True})
    result = await env.fullscreener.run("fullscreen")
    assert result.ok and env.browser.calls == ["settle_fullscreen"]


async def test_success_claim_without_effect_escalates(env: AgentEnv) -> None:
    async def lies() -> ActionResult:
        return ActionResult(True, "dom")  # says ok, state unchanged

    env.browser.fullscreen_dom = lies  # type: ignore[method-assign]
    env.browser.js_ok = True
    result = await env.fullscreener.run("fullscreen")
    assert result.tier == "js" and env.browser.snap.is_fullscreen


async def test_vision_miss_is_reported_not_clicked(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "overlay"
    env.vlm.replies = [json.dumps({"found": False})]
    result = await env.fullscreener.run("fullscreen")
    assert not result.ok and result.fallback_reason == "vlm_not_found"
    assert env.os.clicks == []


async def test_close_ad_pipeline_and_noop_without_ad(env: AgentEnv) -> None:
    assert (await env.fullscreener.run("close_ad")).ok and env.browser.calls == []  # no ad showing
    env.browser.snap = env.browser.snap.model_copy(update={"ad_showing": True})
    env.browser.dom_fail_reason = "overlay"
    env.browser.js_ok = True
    result = await env.fullscreener.run("close_ad")
    assert result.ok and result.tier == "js"
    assert env.browser.calls == ["close_ad_dom", "close_ad_js"]


async def test_close_ad_vision_targets_the_skip_control(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"ad_showing": True})
    env.browser.dom_fail_reason = "iframe_cross_origin"
    env.vlm.replies = [HIT]
    result = await env.fullscreener.run("close_ad")
    assert result.ok and result.tier == "vision"
    assert "Skip" in env.vlm.calls[0][1]
