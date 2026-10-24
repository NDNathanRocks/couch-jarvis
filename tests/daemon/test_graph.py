from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from command_queue import CommandRequest
from executors.base import ActionResult
from llm.base import LLMError
from support import AgentEnv

PLAY_PLAN = {
    "intent": "media.play",
    "confidence": 0.9,
    "slots": {"query": "lofi hip hop"},
    "steps": [
        {
            "op": "navigate",
            "args": {"query": "lofi hip hop", "source": "youtube"},
            "expect": "video_playing",
        }
    ],
    "source": "llm",
}


def text(utterance: str, key: str = "k") -> CommandRequest:
    return CommandRequest(
        command_id=f"c-{key}", idempotency_key=key, device_id="d1", kind="text", text=utterance
    )


def button(action: str) -> CommandRequest:
    return CommandRequest(
        command_id="c-b",
        idempotency_key="b",
        device_id="d1",
        kind="button",
        action=action,  # type: ignore[arg-type]
    )


def spans(env: AgentEnv, name: str) -> list[dict[str, Any]]:
    return [r for r in env.telemetry.recent() if r["name"] == name]


async def test_llm_route_dom_happy_path(env: AgentEnv) -> None:
    env.router.replies = [json.dumps(PLAY_PLAN)]
    result = await env.runner().run(text("play something chill"))
    assert result.ok and result.tier_used == "dom"
    assert result.message == "Playing lofi hip hop"
    assert env.browser.calls == ["search_and_play:youtube:lofi hip hop"]
    assert len(env.router.calls) == 1
    (llm_span,) = spans(env, "llm.call")
    assert llm_span["attributes"]["gen_ai.usage.input_tokens"] == 10
    (root,) = spans(env, "cmd.lifecycle")
    assert root["attributes"]["cj.ok"] is True
    # every node span shares the lifecycle trace
    assert {s["trace_id"] for s in spans(env, "agent.node.intent_router")} == {root["trace_id"]}


async def test_fastpath_never_calls_the_llm(env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"is_playing": True})
    result = await env.runner().run(text("pause"))
    assert result.ok
    assert env.router.calls == []
    assert env.browser.calls == ["media:pause"]
    (router_span,) = spans(env, "agent.node.intent_router")
    assert router_span["attributes"]["cj.intent_source"] == "fastpath"


async def test_button_press_skips_router(env: AgentEnv) -> None:
    result = await env.runner().run(button("next"))
    assert result.ok and env.router.calls == []
    assert env.browser.calls == ["media:next"]


async def test_cross_origin_iframe_falls_back_to_vision(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "iframe_cross_origin"
    env.vlm.replies = [
        json.dumps(
            {"found": True, "mark_id": 5, "bbox_norm": [0.4, 0.4, 0.6, 0.6], "rationale": "x"}
        )
    ]
    result = await env.runner().run(text("fullscreen"))
    assert result.ok and result.tier_used == "vision"
    assert env.browser.calls == ["fullscreen_dom", "fullscreen_js", "settle_fullscreen"]
    assert env.os.clicks == [(960, 540)]  # center of the grounded box on a 1920x1080 screen
    assert env.vlm.image_calls == 1
    tiers = [s["attributes"].get("cj.tier") for s in spans(env, "executor.step")]
    assert "vision" in tiers
    (fallback,) = spans(env, "agent.node.vision_fallback")
    assert fallback["attributes"]["cj.fallback_reason"] == "iframe_cross_origin"
    assert env.telemetry.rollup()["vision_fallback_rate"] is not None


async def test_vision_not_found_ends_in_failure(env: AgentEnv) -> None:
    env.browser.dom_fail_reason = "overlay"
    env.vlm.replies = [json.dumps({"found": False})] * 5
    result = await env.runner(max_attempts=1).run(text("fullscreen"))
    assert not result.ok
    assert env.os.clicks == []


async def test_verifier_failure_triggers_recovery_then_succeeds(env: AgentEnv) -> None:
    env.browser.fail_verify_times = 1  # first "play" reports ok but does nothing
    result = await env.runner().run(text("play"))
    assert result.ok
    assert env.browser.calls.count("media:play") == 2
    assert "refocus" in env.browser.calls and "dismiss_overlays" in env.browser.calls
    assert "reload" not in env.browser.calls  # reload only from the second recovery on
    verifies = [
        s["attributes"]["cj.verify_passed"] for s in spans(env, "agent.node.post_action_verifier")
    ]
    assert verifies == [False, True]


async def test_exhausted_attempts_fail_with_a_toast(env: AgentEnv) -> None:
    env.browser.fail_verify_times = 99
    result = await env.runner(max_attempts=2).run(text("play"))
    assert not result.ok
    assert result.message == "Sorry, that did not work. Try again."
    assert env.browser.calls.count("media:play") == 3  # first try + 2 recoveries
    assert env.browser.calls.count("reload") == 1


async def test_executor_failure_retries_then_succeeds(env: AgentEnv) -> None:
    env.router.replies = [json.dumps(PLAY_PLAN)]
    real = env.browser.search_and_play
    attempts = [0]

    async def flaky(query: str, source: str, *, play: bool = True) -> ActionResult:
        attempts[0] += 1
        if attempts[0] == 1:  # e.g. Edge was mid-relaunch on the first try
            return ActionResult(False, "dom", "no result", "selector_timeout")
        return await real(query, source)

    env.browser.search_and_play = flaky  # type: ignore[method-assign]
    result = await env.runner().run(text("play something chill"))
    assert result.ok
    assert attempts[0] == 2
    assert "refocus" in env.browser.calls  # recovery ran between the attempts
    assert len(env.router.calls) == 1  # the plan is reused, not re-routed


async def test_malformed_router_json_gets_one_repair_retry(env: AgentEnv) -> None:
    env.router.replies = ["I think you want music!", "still not json"]
    result = await env.runner().run(text("do the thing"))
    assert not result.ok and result.message == "Sorry, I did not catch that."
    assert len(env.router.calls) == 2
    assert "invalid" in env.router.calls[1][1]  # second call is the repair prompt


async def test_repair_retry_can_succeed(env: AgentEnv) -> None:
    env.router.replies = ["```not json```", "```json\n" + json.dumps(PLAY_PLAN) + "\n```"]
    result = await env.runner().run(text("play something chill"))
    assert result.ok and len(env.router.calls) == 2


async def test_router_rejects_invented_ops_and_fields(env: AgentEnv) -> None:
    evil = {**PLAY_PLAN, "steps": [{"op": "run_shell", "args": {"cmd": "calc"}, "expect": None}]}
    extra = {**PLAY_PLAN, "tool": "powershell"}
    env.router.replies = [json.dumps(evil), json.dumps(extra)]
    result = await env.runner().run(text("open calculator"))
    assert not result.ok
    assert env.browser.calls == [] and env.os.keys == []


async def test_unknown_intent_is_a_polite_failure(env: AgentEnv) -> None:
    unknown = {"intent": "unknown", "confidence": 0.1, "slots": {}, "steps": [], "source": "llm"}
    env.router.replies = [json.dumps(unknown)]
    result = await env.runner().run(text("blorp"))
    assert not result.ok and "did not catch" in result.message


async def test_llm_outage_is_reported(env: AgentEnv) -> None:
    env.router.replies = [LLMError("down")]
    result = await env.runner().run(text("play something nice"))
    assert not result.ok and "Set up AI brain" in result.message


async def test_hard_timeout_ceiling(env: AgentEnv) -> None:
    async def hang(query: str, source: str, *, play: bool = True) -> ActionResult:
        await asyncio.sleep(10)
        return ActionResult(True, "dom")

    env.browser.search_and_play = hang  # type: ignore[method-assign]
    env.router.replies = [json.dumps(PLAY_PLAN)]
    result = await env.runner(timeout_s=0.1).run(text("play something chill"))
    assert not result.ok and result.message == "That took too long."


async def test_volume_set_is_verified_against_the_os(env: AgentEnv) -> None:
    result = await env.runner().run(text("volume to 30"))
    assert result.ok and env.os.volume == 30


async def test_mute_is_state_aware_not_a_blind_toggle(env: AgentEnv) -> None:
    env.os.muted = True
    result = await env.runner().run(text("mute"))
    assert result.ok and env.os.muted is True  # already muted: no key press
    assert env.os.keys == []


async def test_progress_events_cover_each_node(env: AgentEnv) -> None:
    seen: list[tuple[str, str]] = []
    env.deps.progress = lambda cid, node, status, detail: seen.append((node, status))
    await env.runner().run(text("skip"))
    assert ("intent_router", "start") in seen and ("post_action_verifier", "done") in seen


@pytest.mark.parametrize("utterance", ["volume up", "mute"])
async def test_os_tier_commands_report_os(env: AgentEnv, utterance: str) -> None:
    result = await env.runner().run(text(utterance))
    assert result.ok and result.tier_used == "os"


async def test_show_qr_calls_the_hud_hook_and_skips_the_llm(env: AgentEnv) -> None:
    shown: list[int] = []
    env.deps.show_qr = lambda: shown.append(1)
    result = await env.runner().run(button("show_qr"))
    assert result.ok and shown == [1] and env.router.calls == []
    voice = await env.runner().run(text("pair a friend", "q2"))
    assert voice.ok and shown == [1, 1]
