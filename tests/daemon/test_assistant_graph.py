"""The assistant talks back, remembers, asks, and sends world questions to the web."""

from __future__ import annotations

import json

from agent.memory import ConversationMemory
from command_queue import CommandRequest
from support import AgentEnv, make_env


def request(text: str, kind: str = "text", device: str = "d1") -> CommandRequest:
    return CommandRequest(
        command_id=f"c-{text}",
        idempotency_key=f"k-{text}",
        device_id=device,
        kind=kind,  # type: ignore[arg-type]
        text=text,
    )


def env_with_memory() -> AgentEnv:
    env = make_env()
    env.deps.memory = ConversationMemory(None)
    return env


def plan_json(*steps: dict, intent: str = "unknown", confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "slots": {},
            "steps": list(steps),
            "source": "llm",
        }
    )


async def test_world_question_opens_a_search_tab_without_the_llm() -> None:
    env = env_with_memory()
    result = await env.runner().run(request("what is the capital of greece", "voice"))
    assert result.ok and "capital of greece" in result.message.lower()
    assert env.browser.calls == ["open_search_tab:what is the capital of greece"]
    assert env.router.calls == []


async def test_say_answers_about_the_screen_and_runs_no_actions() -> None:
    env = env_with_memory()
    env.router.replies.append(
        plan_json({"op": "say", "args": {"text": "Season one, episode three."}})
    )
    result = await env.runner().run(request("which episode is it", "voice"))
    assert result.ok and result.message == "Season one, episode three."
    assert result.ask is None and env.browser.calls == []


async def test_ask_returns_a_question_card_and_listens_after_voice() -> None:
    env = env_with_memory()
    env.router.replies.append(
        plan_json(
            {"op": "ask", "args": {"question": "Which song?", "choices": "Thank U, Next|7 rings"}},
            intent="media.play",
        )
    )
    result = await env.runner().run(request("play something by ariana grande", "voice"))
    assert result.ok and result.ask is not None
    assert result.ask.question == "Which song?"
    assert result.ask.choices == ["Thank U, Next", "7 rings"] and result.ask.listen is True
    typed = await make_env().runner().run(request("x"))  # separate env: typed never auto-listens
    assert typed.ask is None


async def test_typed_ask_does_not_open_the_mic() -> None:
    env = env_with_memory()
    env.router.replies.append(plan_json({"op": "ask", "args": {"question": "Which one?"}}))
    result = await env.runner().run(request("play the thing", "text"))
    assert result.ask is not None and result.ask.listen is False


async def test_the_next_utterance_is_routed_with_the_pending_question() -> None:
    env = env_with_memory()
    env.router.replies.append(plan_json({"op": "ask", "args": {"question": "Which song?"}}))
    await env.runner().run(request("play something by ariana grande", "voice"))
    env.router.replies.append(
        plan_json(
            {
                "op": "navigate",
                "args": {"query": "7 rings", "source": "ytmusic", "play": True},
                "expect": "video_playing",
            },
            intent="media.play",
        )
    )
    answer = await env.runner().run(request("the second one", "voice"))
    assert answer.ok
    assert "<pending_question>Which song?</pending_question>" in env.router.calls[-1][1]
    assert env.deps.memory is not None and env.deps.memory.pending("d1") is None


async def test_never_mind_clears_the_question_without_the_llm() -> None:
    env = env_with_memory()
    env.router.replies.append(plan_json({"op": "ask", "args": {"question": "Which song?"}}))
    await env.runner().run(request("play something", "voice"))
    calls = len(env.router.calls)
    result = await env.runner().run(request("never mind", "voice"))
    assert result.ok and len(env.router.calls) == calls
    assert env.deps.memory is not None and env.deps.memory.pending("d1") is None


async def test_recent_actions_reach_the_router_for_retry_on_a_new_tab() -> None:
    env = env_with_memory()
    await env.runner().run(request("play lofi beats on youtube", "voice"))
    env.router.replies.append(
        plan_json(
            {"op": "browser_nav", "args": {"action": "new_tab"}},
            {
                "op": "navigate",
                "args": {"query": "lofi beats", "source": "youtube", "play": True},
                "expect": "video_playing",
            },
            intent="media.play",
        )
    )
    result = await env.runner().run(request("retry that but in a new tab", "voice"))
    prompt = env.router.calls[-1][1]
    assert "<recent_actions>" in prompt and "play lofi beats on youtube" in prompt
    assert (
        result.ok
        and "new_tab" in "".join(env.browser.calls)
        or any("new_tab" in c for c in env.browser.calls)
    )


async def test_forget_that_clears_memory() -> None:
    env = env_with_memory()
    await env.runner().run(request("pause", "voice"))
    assert env.deps.memory is not None and env.deps.memory.turns()
    result = await env.runner().run(request("forget that"))
    assert result.ok and env.deps.memory.turns() == []
