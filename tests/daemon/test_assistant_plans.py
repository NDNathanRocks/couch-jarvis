from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.state import ActionStep, Intent, IntentPlan


def steps(*ops: str) -> list[ActionStep]:
    return [ActionStep(op=op, args={"text": "x", "question": "q"}) for op in ops]  # type: ignore[arg-type]


def test_say_and_a_trailing_ask_are_valid() -> None:
    IntentPlan(intent=Intent.UNKNOWN, confidence=0.9, steps=steps("say", "ask"))


def test_ask_must_be_last_and_unique() -> None:
    with pytest.raises(ValidationError):
        IntentPlan(intent=Intent.UNKNOWN, confidence=0.9, steps=steps("ask", "say"))
    with pytest.raises(ValidationError):
        IntentPlan(intent=Intent.UNKNOWN, confidence=0.9, steps=steps("ask", "ask"))


from agent.fastpath import plan_text


@pytest.mark.parametrize(
    ("said", "query"),
    [
        ("what is the weather like today", "what is the weather like today"),
        ("What's the capital of Greece?", "what's the capital of greece"),
        ("who won the world cup in 2022", "who won the world cup in 2022"),
        ("tell me the latest news", "the latest news"),
        ("how many people live in canada", "how many people live in canada"),
    ],
)
def test_world_questions_become_a_web_search(said: str, query: str) -> None:
    plan = plan_text(said)
    assert plan is not None and plan.source == "fastpath"
    assert [(s.op, s.args["query"]) for s in plan.steps] == [("web_search", query)]


@pytest.mark.parametrize(
    "said", ["what is playing", "what episode is this", "who sings this song", "pause", "what"]
)
def test_screen_questions_and_commands_are_not_web_searches(said: str) -> None:
    plan = plan_text(said)
    assert plan is None or all(s.op != "web_search" for s in plan.steps)
