"""LangGraph plan-act-verify state machine.

intent_router -> dom_executor -> (vision_fallback) -> post_action_verifier -> (recovery_node)

``max_attempts`` is the number of recovery rounds allowed after the first try.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from agent.fastpath import plan_button, plan_text
from agent.memory import ConversationMemory
from agent.policy import (
    key_allowed,
    normalize_keys,
    on_media_site,
    sanitize_untrusted,
    url_allowed,
)
from agent.prompts import (
    ROUTER_SYSTEM,
    router_repair_prompt,
    router_user_prompt,
)
from agent.state import (
    ActionStep,
    AgentState,
    Intent,
    IntentPlan,
    StepOutcome,
    VerifyResult,
)
from command_queue import CommandRequest
from contracts import AskPrompt, CmdResultPayload, Suggestion
from executors.base import ActionResult, Browser, MediaKey, OSController
from executors.page_items import NO_SEARCH_BAR, NO_SUBTITLES
from executors.subtitles import SubtitleController, language_name
from executors.video_fullscreener import VideoFullscreener
from executors.vision_grounding import VisionGrounder
from llm.base import LLMError, TextLLM
from telemetry import AttrValue, Span, Telemetry, new_trace_id
from timers import TimerService
from voice import VoiceService, pref_message
from voice.replies import written_message

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, str, str, str], None]  # command_id, node, status, detail

VISION_ELIGIBLE = {"click_selector", "fullscreen", "close_ad", "vision_click", "subtitles"}
BROWSER_MEDIA = {"play", "pause", "play_pause", "next", "prev"}
OS_FALLBACK_KEYS: dict[str, MediaKey] = {"play_pause": "play_pause", "next": "next", "prev": "prev"}
FAIL_MESSAGE = "Sorry, that did not work. Try again."
LEAVE_PAGE_MESSAGE = "I stayed on this page. Say 'search' or 'YouTube' if you want me to leave it."
# Failures that retrying cannot fix: end the command now with the step's own message.
TERMINAL_REASONS = {"off_site", "item_not_found", "no_history", "no_search_bar"}
BROWSER_NAV_ACTIONS = frozenset(
    {"back", "forward", "reload", "new_tab", "next_tab", "prev_tab", "scroll_up", "scroll_down"}
)
_ASKED_TO_SEARCH = re.compile(r"\b(?:you ?tube|music|search|look up)\b", re.I)
_ASKED_FOR_NEW_TAB = re.compile(r"\bnew (?:tab|window)\b", re.I)
_OPENS_PAGES = frozenset({"navigate", "page_search"})
_NEVER_MIND = re.compile(r"^(?:never ?mind|forget it|cancel(?: that)?|no thanks?|nothing)$", re.I)
_FORGET = re.compile(r"^(?:forget (?:that|everything|all of that)|clear (?:your )?memory)$", re.I)


def split_reply(plan: IntentPlan) -> tuple[IntentPlan, str | None, dict[str, Any] | None]:
    """Take the assistant's own words out of a plan: (actions only, what to say, question)."""
    say = [str(s.args.get("text", "")).strip() for s in plan.steps if s.op == "say"]
    asks = [s for s in plan.steps if s.op == "ask"]
    if not say and not asks:
        return plan, None, None
    actions = [s for s in plan.steps if s.op not in ("say", "ask")]
    ask: dict[str, Any] | None = None
    if asks:
        a = asks[0].args
        raw = str(a.get("choices", ""))
        choices = [c.strip() for c in raw.split("|") if c.strip()][:6] if raw else []
        ask = {"question": str(a.get("question", "")).strip()[:300], "choices": choices}
    return (
        plan.model_copy(update={"steps": actions}),
        " ".join(t for t in say if t)[:400] or None,
        ask,
    )


def with_requested_new_tab(plan: IntentPlan, utterance: str) -> IntentPlan:
    """ "Open a new tab and play X": a model that forgets the tab step would replace the page the
    TV is showing, so the request itself decides, not the model."""
    if (
        not _ASKED_FOR_NEW_TAB.search(utterance)
        or re.search(r"\bclose\b", utterance, re.I)
        or len(plan.steps) >= 8
        or not any(s.op in _OPENS_PAGES for s in plan.steps)
        or any(s.op == "browser_nav" and s.args.get("action") == "new_tab" for s in plan.steps)
    ):
        return plan
    first = next(i for i, s in enumerate(plan.steps) if s.op in _OPENS_PAGES)
    tab = ActionStep(op="browser_nav", args={"action": "new_tab"})
    return plan.model_copy(update={"steps": [*plan.steps[:first], tab, *plan.steps[first:]]})


def _is_web_page(url: str) -> bool:
    return url.startswith(("http://", "https://"))


@dataclass
class AgentDeps:
    router: TextLLM
    browser: Browser
    os_ctl: OSController
    fullscreener: VideoFullscreener
    grounder: VisionGrounder
    telemetry: Telemetry
    timers: TimerService | None = None
    subtitles: SubtitleController | None = None
    subtitle_lang: str = "en"  # default language for "turn on subtitles"
    voice: VoiceService | None = None  # spoken replies; None = silent (tests, no speakers)
    memory: ConversationMemory | None = None  # last few turns + a question awaiting an answer
    show_qr: Callable[[], None] = lambda: None  # set by main when a HUD exists
    progress: ProgressFn = lambda *_: None
    verify_delays_s: tuple[float, ...] = (0.0, 0.4, 0.8)


def extract_json(text: str) -> str:
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = fenced.find("{"), fenced.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return fenced[start : end + 1]


def success_message(plan: IntentPlan) -> str:
    for step in plan.steps:
        if step.op == "voice_pref":
            return pref_message(step.args)
    several = written_message(plan)
    if several is not None:
        return several
    for step in plan.steps:
        if step.op == "subtitles":
            if step.args.get("state") == "off":
                return "Subtitles off"
            lang = str(step.args.get("lang") or "")
            return f"Subtitles on ({language_name(lang)})" if lang else "Subtitles on"
        if step.op == "web_search":
            return f"Searching the web for {step.args.get('query', 'that')}"
        if step.op == "page_search":
            return f"Searched for {step.args.get('query', 'that')}"
    if plan.intent is Intent.MEDIA_PLAY:
        if any(s.op == "click_item" for s in plan.steps):
            return "Opened it"
        if any(s.op == "navigate" and s.args.get("play") is False for s in plan.steps):
            return f"Showing results for {plan.slots.get('query', 'your search')}"
        return f"Playing {plan.slots.get('query', 'your request')}"
    if plan.intent is Intent.TAB:
        nav = next((s.args.get("action") for s in plan.steps if s.op == "browser_nav"), None)
        return {
            "new_tab": "New tab",
            "back": "Went back",
            "forward": "Went forward",
            "reload": "Reloaded",
            "next_tab": "Next tab",
            "prev_tab": "Previous tab",
        }.get(str(nav), "Tab closed" if nav is None else "Done")
    return {
        Intent.VOLUME: "Volume updated",
        Intent.FULLSCREEN: "Fullscreen",
        Intent.TIMER: "Timer set",
        Intent.SYSTEM: "Done",
    }.get(plan.intent, "Done")


def failure_detail(state: AgentState) -> str:
    """One short line for the phone's log: what we understood and which step failed why."""
    parts: list[str] = []
    if state.plan is not None:
        steps = ",".join(s.op for s in state.plan.steps) or "none"
        parts.append(f"{state.plan.intent.value} via {state.plan.source} [{steps}]")
    else:
        parts.append("no plan")
    for o in state.outcomes:
        if not o.ok:
            parts.append(f"step{o.step_index}/{o.tier}: {o.error or 'failed'}")
    if state.verify is not None and not state.verify.passed:
        parts.append(f"verify {state.verify.predicate} failed")
    return "; ".join(parts)[:400]


def _overall_tier(outcomes: list[StepOutcome]) -> str:
    tiers = {o.tier for o in outcomes if o.ok}
    for tier in ("vision", "js", "dom", "os"):
        if tier in tiers:
            return tier
    return "none"


def build_graph(deps: AgentDeps) -> Any:
    tele = deps.telemetry

    # ------------------------------------------------------------------ helpers

    def node(
        name: str,
    ) -> Callable[
        [Callable[[AgentState, Span], Awaitable[dict[str, Any]]]],
        Callable[[AgentState], Awaitable[dict[str, Any]]],
    ]:
        def deco(
            fn: Callable[[AgentState, Span], Awaitable[dict[str, Any]]],
        ) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
            async def wrapped(state: AgentState) -> dict[str, Any]:
                deps.progress(state.command_id, name, "start", "")
                attrs: dict[str, AttrValue] = {
                    "cj.command_id": state.command_id,
                    "cj.attempt": state.attempts,
                }
                with tele.span(f"agent.node.{name}", attributes=attrs) as span:
                    update = await fn(state, span)
                deps.progress(state.command_id, name, "done", str(update.get("status", "")))
                return update

            return wrapped

        return deco

    async def route_with_llm(state: AgentState, span: Span) -> IntentPlan | None:
        snap = await deps.browser.snapshot()
        # The tab title is page-controlled text: quote it as bounded, markup-free untrusted data
        # and never include the URL.
        title = sanitize_untrusted(snap.title)
        try:
            items = await deps.browser.list_items(30)
        except Exception:  # the page is a convenience for routing, never a reason to fail
            items = []
        listing = " | ".join(f"{c.index}: {sanitize_untrusted(c.text, 50)}" for c in items)
        context = (
            f"site={'media' if on_media_site(snap.url) else 'other'} "  # never the hostname
            f"<untrusted_page_title>{title}</untrusted_page_title> "
            f"<untrusted_page_items>{listing}</untrusted_page_items> "
            f"playing={snap.is_playing} fullscreen={snap.is_fullscreen}"
        )
        if deps.memory is not None:
            recent = deps.memory.render()
            pending = deps.memory.pending(state.issued_by)
            if recent:
                context += "\n" + recent
            if pending is not None:
                context += (
                    f"\n<pending_question>{sanitize_untrusted(pending.question, 200)}"
                    f"</pending_question>"
                )
        user = router_user_prompt(state.utterance, context)
        bad_reply, error = "", ""
        for attempt in range(2):  # one repair retry
            prompt = (
                user if attempt == 0 else router_repair_prompt(state.utterance, bad_reply, error)
            )
            with tele.span("llm.call", attributes={"cj.purpose": "router"}) as llm_span:
                reply = await deps.router.complete(ROUTER_SYSTEM, prompt)
                llm_span.set_attribute("gen_ai.system", reply.system)
                llm_span.set_attribute("gen_ai.request.model", reply.model)
                llm_span.set_attribute("gen_ai.usage.input_tokens", reply.input_tokens)
                llm_span.set_attribute("gen_ai.usage.output_tokens", reply.output_tokens)
            try:
                plan = IntentPlan.model_validate(json.loads(extract_json(reply.text)))
                plan = plan.model_copy(update={"source": "llm"})
                return with_requested_new_tab(plan, state.utterance)
            except (ValueError, ValidationError) as exc:
                bad_reply, error = reply.text, str(exc)
                span.set_attribute("cj.router_repairs", attempt + 1)
        return None

    # ------------------------------------------------------------------ nodes

    @node("intent_router")
    async def intent_router(state: AgentState, span: Span) -> dict[str, Any]:
        plan = state.plan
        said = state.utterance.strip().strip(" .!?").lower()
        if deps.memory is not None and plan is None:
            if _FORGET.match(said):
                deps.memory.clear()
                return {"status": "done", "final_message": "Okay, forgotten."}
            if _NEVER_MIND.match(said) and deps.memory.pending(state.issued_by) is not None:
                deps.memory.clear_pending(state.issued_by)
                return {"status": "done", "final_message": "Okay, never mind."}
        if plan is None:
            plan = plan_text(state.utterance)
            waiting = deps.memory is not None and deps.memory.pending(state.issued_by) is not None
            if plan is not None and waiting and any(s.op == "click_item" for s in plan.steps):
                plan = None  # "the second one" answers my question, it is not a click on the page
        if plan is None:
            try:
                plan = await route_with_llm(state, span)
            except LLMError as exc:
                span.set_error(exc)
                return {"status": "failed", "final_message": "My brain is offline right now."}
        span.set_attribute("cj.intent", plan.intent.value if plan else "unknown")
        span.set_attribute("cj.intent_source", plan.source if plan else "llm")
        if deps.memory is not None:
            deps.memory.clear_pending(state.issued_by)  # the answer is being handled right now
        reply: str | None = None
        ask: dict[str, Any] | None = None
        if plan is not None:
            plan, reply, ask = split_reply(plan)
        if plan is not None and (reply or ask):  # the assistant is talking: no confidence gate
            words = reply or (ask or {}).get("question") or ""
            update: dict[str, Any] = {"plan": plan, "reply": reply, "ask": ask}
            if not plan.steps:
                return {**update, "status": "done", "final_message": str(words)}
            return {**update, "status": "executing"}
        if (
            plan is None
            or plan.intent is Intent.UNKNOWN
            or not plan.steps
            or plan.confidence < 0.35
        ):
            return {
                "plan": plan,
                "status": "failed",
                "final_message": "Sorry, I did not catch that.",
            }
        return {"plan": plan, "status": "executing"}

    async def run_step(step: ActionStep, utterance: str = "") -> ActionResult:
        a = step.args
        op = step.op
        if op == "navigate":
            if "query" in a:
                url = (await deps.browser.snapshot()).url
                if (
                    _is_web_page(url)
                    and not on_media_site(url)
                    and not _ASKED_TO_SEARCH.search(utterance)
                ):
                    # Playing "episode 5" must never yank the TV off the site it is showing.
                    return ActionResult(False, "dom", LEAVE_PAGE_MESSAGE, "off_site")
                return await deps.browser.search_and_play(
                    str(a["query"]), str(a.get("source", "youtube")), play=bool(a.get("play", True))
                )
            url = str(a.get("url", ""))
            if not url_allowed(url):  # LLM output is untrusted: only https media sites
                return ActionResult(False, "dom", "url is not on the media allowlist", "blocked")
            return await deps.browser.navigate(url)
        if op == "media_key":
            return await run_media_key(a)
        if op == "volume_set":
            return await deps.os_ctl.set_volume(int(a["level"]))
        if op == "seek":
            return await deps.browser.seek(int(a["delta_s"]))
        if op in ("fullscreen", "close_ad"):
            return await deps.fullscreener.dom_js_tiers(op)
        if op == "close_tab":
            return await deps.browser.close_tab()
        if op == "web_search":
            return await deps.browser.open_search_tab(str(a.get("query", "")).strip())
        if op == "page_search":
            query = str(a.get("query", "")).strip()
            found = await deps.browser.page_search(query)
            if not found.ok and found.fallback_reason == "no_search_bar":
                # Ask, do not guess: offer a one-tap follow-up instead of opening tabs unasked.
                offer = {
                    "label": "Open a new tab and search YouTube",
                    "text": f"search for {query} on youtube in a new tab",
                }
                return ActionResult(
                    False,
                    found.tier,
                    found.error or NO_SEARCH_BAR,
                    "no_search_bar",
                    {"suggest": offer},
                )
            return found
        if op == "subtitles":
            want = str(a.get("state", "toggle"))
            lang = str(a.get("lang") or deps.subtitle_lang)
            if deps.subtitles is not None:
                return await deps.subtitles.dom_tier(want, lang)
            return await deps.browser.set_subtitles(want, lang)
        if op == "browser_nav":
            action = str(a.get("action", ""))
            if action not in BROWSER_NAV_ACTIONS:
                return ActionResult(
                    False, "dom", f"unknown browser action: {action}", "unsupported"
                )
            return await deps.browser.browser_action(action)
        if op == "sleep_display":
            return await deps.os_ctl.sleep_display()
        if op == "voice_pref":
            if deps.voice is None:
                return ActionResult(
                    False, "os", "Spoken replies are not set up here", "unsupported"
                )
            return deps.voice.apply(a)
        if op == "show_qr":
            deps.show_qr()
            return ActionResult(True, "os")
        if op == "click_selector":
            if not on_media_site((await deps.browser.snapshot()).url):
                return ActionResult(
                    False, "dom", "clicks are only allowed on media sites", "blocked"
                )
            return await deps.browser.click_selector(str(a["selector"]), step.timeout_ms)
        if op == "key":
            keys = normalize_keys(str(a.get("keys", "")))
            if not key_allowed(keys):  # never let model output press arbitrary OS shortcuts
                return ActionResult(
                    False, "os", f"key combination {keys!r} is not allowed", "blocked"
                )
            return await deps.os_ctl.key_combo(*keys.split("+"))
        if op == "js_eval_allowlisted":
            return await deps.browser.eval_allowlisted(str(a.get("name", "")))
        if op == "wait_for":
            await asyncio.sleep(min(float(a.get("ms", 500)), 5000) / 1000)
            return ActionResult(True, "dom")
        if op == "timer_start":
            if deps.timers is None:
                return ActionResult(False, "os", "timers unavailable")
            deps.timers.start(
                float(a["minutes"]), str(a.get("label", "timer")), str(a.get("action", "notify"))
            )
            return ActionResult(True, "os")
        if op == "click_item":
            # Index/episode/text pick from the list the browser itself reported: the model never
            # supplies a selector, so this is allowed on any web page (not only media sites).
            if not _is_web_page((await deps.browser.snapshot()).url):
                return ActionResult(False, "dom", "no web page to click on", "off_site")
            picks: dict[str, Any] = {}
            for key in ("index", "episode", "result"):
                if key in a:
                    picks[key] = int(a[key])
            if "text" in a:
                picks["text"] = str(a["text"])
            if not picks:
                return ActionResult(False, "dom", "nothing to pick", "unsupported")
            return await deps.browser.click_item(**picks)
        if op == "vision_click":
            if not on_media_site((await deps.browser.snapshot()).url):
                return ActionResult(
                    False, "dom", "clicks are only allowed on media sites", "blocked"
                )
            return ActionResult(False, "dom", "vision-only step", "vision_only")
        return ActionResult(False, "dom", f"unsupported op: {op}", "unsupported")

    async def run_media_key(a: dict[str, str | int | float | bool]) -> ActionResult:
        key = str(a["key"])
        if key in BROWSER_MEDIA:
            res = await deps.browser.media_command(key)  # type: ignore[arg-type]
            if res.ok or key not in OS_FALLBACK_KEYS:
                return res
            return await deps.os_ctl.press_media_key(OS_FALLBACK_KEYS[key])  # OS media key fallback
        if key == "mute":
            wanted = str(a.get("state", "toggle"))
            _, muted = await deps.os_ctl.get_volume()
            if wanted == "toggle" or (wanted == "on") != muted:
                return await deps.os_ctl.press_media_key("mute")
            return ActionResult(True, "os")
        result = ActionResult(True, "os")
        for _ in range(max(1, min(int(a.get("repeat", 1)), 25))):
            result = await deps.os_ctl.press_media_key(key)  # type: ignore[arg-type]
            if not result.ok:
                break
        return result

    @node("dom_executor")
    async def dom_executor(state: AgentState, span: Span) -> dict[str, Any]:
        assert state.plan is not None
        steps = state.plan.steps
        outcomes = list(state.outcomes)
        cursor, tier_used = state.cursor, state.tier_used
        while cursor < len(steps):
            step = steps[cursor]
            started = time.perf_counter()
            with tele.span("executor.step", attributes={"cj.op": step.op}) as step_span:
                res = await run_step(step, state.utterance)
                step_span.set_attribute("cj.tier", res.tier)
                if not res.ok:
                    step_span.set_attribute("cj.fallback_reason", res.fallback_reason)
                    step_span.set_error(res.error or "step failed")
            outcomes.append(
                StepOutcome(
                    step_index=cursor,
                    tier=res.tier,
                    ok=res.ok,
                    error=res.error,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
            if res.ok:
                cursor += 1
                tier_used = res.tier
                continue
            if res.fallback_reason in TERMINAL_REASONS:
                # Retrying cannot help (and recovery would reload the page): tell the user why.
                return {
                    "cursor": cursor,
                    "outcomes": outcomes,
                    "status": "failed",
                    "final_message": res.error or FAIL_MESSAGE,
                    "suggest": res.observed.get("suggest"),
                }
            if step.op in VISION_ELIGIBLE and res.fallback_reason != "blocked":
                return {
                    "cursor": cursor,
                    "outcomes": outcomes,
                    "needs_vision": True,
                    "fallback_reason": res.fallback_reason,
                    "status": "vision",
                }
            return {
                "cursor": cursor,
                "outcomes": outcomes,
                "status": "verifying",
                "tier_used": tier_used,
            }
        return {
            "cursor": cursor,
            "outcomes": outcomes,
            "status": "verifying",
            "tier_used": tier_used,
            "needs_vision": False,
        }

    @node("vision_fallback")
    async def vision_fallback(state: AgentState, span: Span) -> dict[str, Any]:
        assert state.plan is not None
        step = state.plan.steps[state.cursor]
        span.set_attribute("cj.fallback_reason", state.fallback_reason)
        started = time.perf_counter()
        if step.op in ("fullscreen", "close_ad"):
            res = await deps.fullscreener.vision_tier(step.op)
        elif step.op == "subtitles" and deps.subtitles is not None:
            res = await deps.subtitles.vision_tier(
                str(step.args.get("state", "toggle")),
                str(step.args.get("lang") or deps.subtitle_lang),
            )
            if not res.ok:  # a missing control will not appear on a retry: say so now
                missed = StepOutcome(
                    step_index=state.cursor,
                    tier="vision",
                    ok=False,
                    error=res.error,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                return {
                    "outcomes": [*state.outcomes, missed],
                    "needs_vision": False,
                    "status": "failed",
                    "final_message": NO_SUBTITLES,
                }
        elif not on_media_site((await deps.browser.snapshot()).url):
            res = ActionResult(False, "vision", "clicks are only allowed on media sites", "blocked")
        else:
            target = str(
                step.args.get("target")
                or step.args.get("description")
                or step.args.get("selector", "")
            )
            point = await deps.grounder.ground(target)
            if point is None:
                res = ActionResult(False, "vision", "target not found by VLM", "vlm_not_found")
            else:
                clicked = await deps.os_ctl.click(point.x, point.y)
                res = ActionResult(clicked.ok, "vision", clicked.error)
        outcomes = [
            *state.outcomes,
            StepOutcome(
                step_index=state.cursor,
                tier="vision",
                ok=res.ok,
                error=res.error,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        ]
        if res.ok:
            return {
                "cursor": state.cursor + 1,
                "outcomes": outcomes,
                "needs_vision": False,
                "tier_used": "vision",
                "status": "executing",
            }
        return {"outcomes": outcomes, "needs_vision": False, "status": "verifying"}

    async def check_predicate(predicate: str, plan: IntentPlan) -> VerifyResult:
        snap = await deps.browser.snapshot()
        observed: dict[str, Any] = {
            "is_playing": snap.is_playing,
            "is_fullscreen": snap.is_fullscreen,
            "ad_showing": snap.ad_showing,
        }
        passed: bool
        if predicate == "video_playing":
            passed = snap.is_playing is True
        elif predicate == "paused":
            passed = snap.is_playing is not True
        elif predicate == "is_fullscreen":
            passed = snap.is_fullscreen
        elif predicate == "no_ad":
            passed = not snap.ad_showing
        elif predicate in ("volume_set", "muted", "unmuted"):
            level, muted = await deps.os_ctl.get_volume()
            observed.update(volume=level, muted=muted)
            if predicate == "volume_set":
                want = next(
                    (int(s.args["level"]) for s in plan.steps if s.op == "volume_set"), level
                )
                passed = abs(level - want) <= 2
            else:
                passed = muted if predicate == "muted" else not muted
        elif predicate == "navigated":
            passed = bool(snap.url)
        else:
            passed = True  # unknown predicates never block a command
        return VerifyResult(passed=passed, predicate=predicate, observed=observed)

    @node("post_action_verifier")
    async def post_action_verifier(state: AgentState, span: Span) -> dict[str, Any]:
        assert state.plan is not None
        plan = state.plan
        if state.status == "failed":  # the executor already ended the command with a message
            return {}
        if state.cursor < len(plan.steps):
            result = VerifyResult(passed=False, predicate="execution_incomplete")
        else:
            predicate = next((s.expect for s in reversed(plan.steps) if s.expect), None)
            if predicate is None:
                result = VerifyResult(passed=True, predicate="none")
            else:
                result = await check_predicate(predicate, plan)
                for delay in deps.verify_delays_s[1:]:  # state often lags the action by a moment
                    if result.passed:
                        break
                    await asyncio.sleep(delay)
                    result = await check_predicate(predicate, plan)
        span.set_attribute("cj.verify_passed", result.passed)
        span.set_attribute("cj.verify_predicate", result.predicate)
        if result.passed:
            return {
                "verify": result,
                "status": "done",
                "final_message": success_message(plan),
                "tier_used": _overall_tier(state.outcomes),
            }
        terminal = state.attempts >= state.max_attempts
        return {
            "verify": result,
            "status": "failed" if terminal else "verifying",
            "final_message": FAIL_MESSAGE if terminal else None,
        }

    @node("recovery_node")
    async def recovery_node(state: AgentState, span: Span) -> dict[str, Any]:
        assert state.plan is not None
        await deps.browser.refocus()
        await deps.browser.dismiss_overlays()
        if state.attempts >= 1:
            await deps.browser.reload()
        # verification failures re-run the last step; execution failures retry the failed step
        cursor = max(0, min(state.cursor, len(state.plan.steps) - 1))
        return {
            "attempts": state.attempts + 1,
            "cursor": cursor,
            "needs_vision": False,
            "verify": None,
            "status": "recovering",
        }

    # ------------------------------------------------------------------ edges

    def after_router(state: AgentState) -> str:
        return "dom_executor" if state.status == "executing" else END

    def after_executor(state: AgentState) -> str:
        return "vision_fallback" if state.needs_vision else "post_action_verifier"

    def after_vision(state: AgentState) -> str:
        assert state.plan is not None
        if state.status == "executing" and state.cursor < len(state.plan.steps):
            return "dom_executor"
        return "post_action_verifier"

    def after_verifier(state: AgentState) -> str:
        return "recovery_node" if state.status == "verifying" else END

    graph = StateGraph(AgentState)

    def add_node(name: str, fn: Callable[[AgentState], Awaitable[dict[str, Any]]]) -> None:
        # langgraph's overloads reject decorated closures that return partial-state dicts
        graph.add_node(name, fn)  # type: ignore[call-overload]

    add_node("intent_router", intent_router)
    add_node("dom_executor", dom_executor)
    add_node("vision_fallback", vision_fallback)
    add_node("post_action_verifier", post_action_verifier)
    add_node("recovery_node", recovery_node)
    graph.set_entry_point("intent_router")
    graph.add_conditional_edges("intent_router", after_router, ["dom_executor", END])
    graph.add_conditional_edges(
        "dom_executor", after_executor, ["vision_fallback", "post_action_verifier"]
    )
    graph.add_conditional_edges(
        "vision_fallback", after_vision, ["dom_executor", "post_action_verifier"]
    )
    graph.add_conditional_edges("post_action_verifier", after_verifier, ["recovery_node", END])
    graph.add_edge("recovery_node", "dom_executor")
    return graph.compile()


class AgentRunner:
    """Runs one command through the graph under a hard time ceiling."""

    def __init__(self, deps: AgentDeps, *, timeout_s: float = 25.0, max_attempts: int = 2) -> None:
        self._deps = deps
        self._graph = build_graph(deps)
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts

    def set_progress(self, progress: ProgressFn) -> None:
        self._deps.progress = progress

    def _announce(
        self,
        req: CommandRequest,
        plan: IntentPlan | None,
        ok: bool,
        message: str,
        spoken: str | None = None,
    ) -> None:
        voice = self._deps.voice
        if voice is not None:
            voice.announce(kind=req.kind, plan=plan, ok=ok, message=message, spoken=spoken)

    async def _remember(
        self, req: CommandRequest, final: AgentState, ok: bool, ask: AskPrompt | None
    ) -> None:
        memory = self._deps.memory
        if memory is None or _FORGET.match(final.utterance.strip().strip(" .!?")):
            return
        title = ""
        with contextlib.suppress(Exception):
            title = (await self._deps.browser.snapshot()).title
        memory.record(
            req.device_id,
            final.utterance,
            final.plan,
            ok,
            final.final_message or FAIL_MESSAGE,
            title,
        )
        if ask is not None:
            memory.set_pending(req.device_id, ask.question, ask.choices)

    def initial_state(self, req: CommandRequest) -> AgentState:
        plan = plan_button(req.action) if req.kind == "button" and req.action else None
        utterance = f"button:{req.action}" if plan else (req.text or "")
        return AgentState(
            trace_id=new_trace_id(),
            command_id=req.command_id,
            utterance=utterance,
            issued_by=req.device_id,
            plan=plan,
            max_attempts=self._max_attempts,
        )

    async def run(self, req: CommandRequest) -> CmdResultPayload:
        state = self.initial_state(req)
        started = time.perf_counter()
        tele = self._deps.telemetry
        with tele.span(
            "cmd.lifecycle",
            trace_id=state.trace_id,
            attributes={
                "cj.command_id": req.command_id,
                "cj.device_id": req.device_id,
                "cj.kind": req.kind,
            },
        ) as span:
            try:
                async with asyncio.timeout(self._timeout_s):
                    raw = await self._graph.ainvoke(state, config={"recursion_limit": 40})
            except TimeoutError:
                span.set_error("timeout")
                self._announce(req, None, False, "That took too long.")
                return CmdResultPayload(
                    command_id=req.command_id,
                    ok=False,
                    message="That took too long.",
                    latency_ms=self._timeout_s * 1000,
                )
            final = raw if isinstance(raw, AgentState) else AgentState.model_validate(raw)
            ok = final.status == "done"
            spoken: str | None = None
            ask_prompt: AskPrompt | None = None
            if ok and (final.reply or final.ask):
                if final.ask:
                    ask_prompt = AskPrompt(
                        question=final.ask["question"] or "What do you mean?",
                        choices=final.ask["choices"],
                        listen=req.kind == "voice",
                    )
                spoken = " ".join(
                    t for t in (final.reply, ask_prompt.question if ask_prompt else None) if t
                )
                final = final.model_copy(update={"final_message": spoken})
            span.set_attribute("cj.tier", final.tier_used)
            span.set_attribute("cj.ok", ok)
            if not ok:
                span.set_error(final.final_message or "failed")
            self._announce(req, final.plan, ok, final.final_message or FAIL_MESSAGE, spoken)
            await self._remember(req, final, ok, ask_prompt)
            return CmdResultPayload(
                command_id=req.command_id,
                ok=ok,
                message=final.final_message or FAIL_MESSAGE,
                tier_used=final.tier_used if ok else _overall_tier(final.outcomes),  # type: ignore[arg-type]
                latency_ms=(time.perf_counter() - started) * 1000,
                detail="" if ok else failure_detail(final),
                suggest=Suggestion(**final.suggest) if final.suggest else None,
                ask=ask_prompt,
            )
