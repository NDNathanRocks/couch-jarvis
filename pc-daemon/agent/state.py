"""Pydantic models for the LangGraph plan-act-verify state machine."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intent(StrEnum):
    MEDIA_PLAY = "media.play"
    MEDIA_CONTROL = "media.control"
    VOLUME = "volume"
    FULLSCREEN = "video.fullscreen"
    TAB = "browser.tab"
    NAVIGATE = "browser.navigate"
    TIMER = "timer"
    SYSTEM = "system"
    UNKNOWN = "unknown"


StepOp = Literal[
    "navigate",
    "click_selector",
    "type",
    "key",
    "media_key",
    "volume_set",
    "js_eval_allowlisted",
    "fullscreen",
    "close_ad",
    "vision_click",
    "click_item",
    "browser_nav",
    "subtitles",
    "page_search",
    "wait_for",
    "timer_start",
    # additions to the approved plan so every QuickAction maps to one explicit step
    "seek",
    "close_tab",
    "sleep_display",
    "show_qr",
    "voice_pref",
    # the assistant talking back, and a search for questions the page cannot answer
    "say",
    "ask",
    "web_search",
]

Tier = Literal["dom", "js", "vision", "os"]


class ActionStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    op: StepOp
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)
    expect: str | None = None  # verifier predicate id, e.g. "video_playing"
    timeout_ms: int = Field(default=5000, ge=100, le=30000)


class IntentPlan(BaseModel):
    """Router output. Strict: the LLM may not invent fields."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0, le=1)
    slots: dict[str, str | int | float] = Field(default_factory=dict)
    steps: list[ActionStep] = Field(default_factory=list, max_length=8)
    source: Literal["fastpath", "llm"] = "llm"

    @model_validator(mode="after")
    def _one_question_last(self) -> IntentPlan:
        asks = [i for i, s in enumerate(self.steps) if s.op == "ask"]
        if len(asks) > 1 or (asks and asks[0] != len(self.steps) - 1):
            raise ValueError("at most one ask, and it must be the last step")
        return self


class GroundingResult(BaseModel):
    """VLM output for UI grounding."""

    model_config = ConfigDict(extra="ignore")

    found: bool
    mark_id: int | None = None
    bbox_norm: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 in 0..1
    rationale: str = Field(default="", max_length=200)


class StepOutcome(BaseModel):
    step_index: int
    tier: Tier
    ok: bool
    error: str | None = None
    latency_ms: float


class VerifyResult(BaseModel):
    passed: bool
    predicate: str
    observed: dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    trace_id: str
    command_id: str
    utterance: str
    issued_by: str
    plan: IntentPlan | None = None
    cursor: int = 0
    outcomes: list[StepOutcome] = Field(default_factory=list)
    verify: VerifyResult | None = None
    attempts: int = 0
    max_attempts: int = 2
    status: Literal[
        "routing", "executing", "vision", "verifying", "recovering", "done", "failed"
    ] = "routing"
    final_message: str | None = None
    suggest: dict[str, str] | None = None  # {"label", "text"} offered with a failed result
    reply: str | None = None  # what the assistant says (a ``say`` step)
    ask: dict[str, Any] | None = None  # {"question", "choices"} (an ``ask`` step)
    # execution bookkeeping (extends the approved schema)
    needs_vision: bool = False
    fallback_reason: str | None = None
    tier_used: Literal["dom", "js", "vision", "os", "none"] = "none"
