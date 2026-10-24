from __future__ import annotations

from pathlib import Path

from agent.memory import MAX_TURNS, ConversationMemory
from agent.state import ActionStep, Intent, IntentPlan


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def plan(op: str = "navigate", **args: str | bool) -> IntentPlan:
    return IntentPlan(
        intent=Intent.MEDIA_PLAY, confidence=0.9, steps=[ActionStep(op=op, args=args)]  # type: ignore[arg-type]
    )


def test_keeps_only_the_last_turns_and_renders_them() -> None:
    mem = ConversationMemory(None, Clock())
    for i in range(MAX_TURNS + 3):
        mem.record("d", f"play song {i}", plan(query=f"song {i}"), True, "ok")
    assert len(mem.turns()) == MAX_TURNS
    text = mem.render()
    assert "<recent_actions>" in text and "song 8" in text and "song 0" not in text


def test_failed_turns_are_marked_and_empty_memory_renders_nothing() -> None:
    mem = ConversationMemory(None, Clock())
    assert mem.render() == ""
    mem.record("d", "play x", plan(query="x"), False, "nope")
    assert "FAILED" in mem.render()


def test_memory_expires_when_idle() -> None:
    clock = Clock()
    mem = ConversationMemory(None, clock)
    mem.record("d", "pause", None, True, "ok")
    clock.t += 31 * 60
    assert mem.turns() == [] and mem.render() == ""


def test_pending_question_expires_and_clears_per_device() -> None:
    clock = Clock()
    mem = ConversationMemory(None, clock)
    mem.set_pending("a", "Which song?", ["one", "two"])
    assert mem.pending("a") is not None and mem.pending("b") is None
    mem.clear_pending("a")
    assert mem.pending("a") is None
    mem.set_pending("a", "Which?", [])
    clock.t += 61
    assert mem.pending("a") is None


def test_persists_and_survives_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    clock = Clock()
    ConversationMemory(path, clock).record("d", "play a", plan(query="a"), True, "ok")
    assert len(ConversationMemory(path, clock).turns()) == 1
    path.write_text("{not json", encoding="utf-8")
    assert ConversationMemory(path, clock).turns() == []


def test_clear_forgets_everything() -> None:
    mem = ConversationMemory(None, Clock())
    mem.record("d", "x", None, True, "ok")
    mem.set_pending("d", "q", [])
    mem.clear()
    assert mem.turns() == [] and mem.pending("d") is None
