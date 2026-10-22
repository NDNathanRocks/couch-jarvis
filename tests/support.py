"""Shared test helpers (importable as ``support``; tests/ is on pythonpath)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.graph import AgentDeps, AgentRunner
from executors.fakes import FakeBrowser, FakeOS, FakeScreen
from executors.subtitles import SubtitleController
from executors.video_fullscreener import VideoFullscreener
from executors.vision_grounding import VisionGrounder
from llm.fake import ScriptedLLM
from telemetry import Telemetry


@dataclass
class AgentEnv:
    browser: FakeBrowser
    os: FakeOS
    screen: FakeScreen
    router: ScriptedLLM
    vlm: ScriptedLLM
    telemetry: Telemetry
    deps: AgentDeps
    fullscreener: VideoFullscreener
    grounder: VisionGrounder

    def runner(self, *, timeout_s: float = 5.0, max_attempts: int = 2) -> AgentRunner:
        return AgentRunner(self.deps, timeout_s=timeout_s, max_attempts=max_attempts)


def make_env() -> AgentEnv:
    """Agent wired entirely to in-memory fakes (no Edge, no GPU, no network)."""
    tele = Telemetry(None)
    browser = FakeBrowser()
    os_ctl = FakeOS(browser)
    screen = FakeScreen()
    router, vlm = ScriptedLLM([]), ScriptedLLM([])
    grounder = VisionGrounder(vlm, screen, tele)
    fullscreener = VideoFullscreener(browser, grounder, os_ctl, tele, settle_s=0)
    deps = AgentDeps(router, browser, os_ctl, fullscreener, grounder, tele, verify_delays_s=(0.0,))
    deps.subtitles = SubtitleController(browser, grounder, os_ctl, settle_s=0)
    return AgentEnv(browser, os_ctl, screen, router, vlm, tele, deps, fullscreener, grounder)
