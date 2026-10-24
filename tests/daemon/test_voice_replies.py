"""Spoken replies: phrasing, provider fallback, saved preferences and the agent hook."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agent.fastpath import plan_text
from command_queue import CommandRequest
from contracts import VoiceState
from support import make_env
from voice.player import NullPlayer
from voice.replies import Phraser
from voice.service import VoicePrefsStore, VoiceService, pref_message
from voice.tts import (
    ElevenLabsProvider,
    Speech,
    TtsEngine,
    TtsError,
    TtsQuotaError,
    UsageLedger,
    VoiceSpec,
)

# ------------------------------------------------------------------ phrasing


def phraser() -> Phraser:
    return Phraser(random.Random(7))


def test_every_style_names_what_is_playing() -> None:
    plan = plan_text("play the weeknd on youtube music")
    assert plan is not None
    for style in ("jarvis", "friendly", "brief"):
        line = phraser().success(style, plan)
        assert "the weeknd" in line.lower(), (style, line)
    assert "YouTube Music" in phraser().success("jarvis", plan)


def test_several_steps_make_one_sentence() -> None:
    plan = plan_text("open a new tab and play the weeknd on youtube music")
    assert plan is not None
    line = phraser().success("jarvis", plan)
    assert line.startswith("A new tab") or line.startswith("New tab")
    assert ", and " in line and "The Weeknd" in line and line.endswith(".")


def test_phrases_vary_but_never_repeat_back_to_back() -> None:
    plan = plan_text("pause")
    p = phraser()
    lines = [p.success("jarvis", plan) for _ in range(12)]
    assert len(set(lines)) > 1
    assert all(a != b for a, b in zip(lines, lines[1:], strict=False))


def test_failures_say_why_without_a_double_apology() -> None:
    p = phraser()
    assert p.failure("jarvis", "No search bar found.") == (
        "I'm afraid that didn't work. No search bar found."
    )
    assert p.failure("jarvis", "I stayed on this page.") == "I stayed on this page."
    assert p.failure("jarvis", "Sorry, I did not catch that.") == "Sorry, I did not catch that."
    assert p.failure("brief", "No search bar") == "No search bar."


def test_an_unknown_plan_still_gets_a_confirmation() -> None:
    assert phraser().success("brief", None) == "Done."


def test_setting_commands_are_understood() -> None:
    cases = {
        "stop talking": {"enabled": False},
        "turn voice replies off": {"enabled": False},
        "voice replies on": {"enabled": True},
        "talk to me": {"enabled": True},
        "be more friendly": {"style": "friendly"},
        "set the style to brief": {"style": "brief"},
        "use the british female voice": {"voice": "british_female"},
        "switch to friday": {"voice": "british_female"},
        "test the voice": {"test": True},
    }
    for text, args in cases.items():
        plan = plan_text(text)
        assert plan is not None, text
        assert plan.steps[0].op == "voice_pref" and plan.steps[0].args == args, text
    assert pref_message({"enabled": False}) == "Voice replies off"
    assert plan_text("mute") is not None and plan_text("mute").steps[0].op == "media_key"  # type: ignore[union-attr]


# ------------------------------------------------------------------ providers


class FakeProvider:
    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name  # type: ignore[assignment]
        self.error = error
        self.calls = 0

    async def synthesize(self, text: str, voice: VoiceSpec) -> Speech:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Speech(f"{self.name}:{voice.edge}:{text}".encode(), ".mp3", self.name)  # type: ignore[arg-type]


async def test_falls_through_to_the_next_voice_and_remembers_who_is_down() -> None:
    now = [0.0]
    eleven = FakeProvider("elevenlabs", TtsQuotaError("used up"))
    edge = FakeProvider("edge", TtsError("offline"))
    windows = FakeProvider("windows")
    engine = TtsEngine([eleven, edge, windows], clock=lambda: now[0])  # type: ignore[list-item]

    speech = await engine.synthesize("Hello there", "british_male")
    assert speech is not None and speech.engine == "windows"
    assert engine.active == "windows"
    await engine.synthesize("Again", "british_male")
    assert (eleven.calls, edge.calls, windows.calls) == (1, 1, 2)  # the dead ones are skipped

    now[0] += 61  # a network blip is retried soon...
    await engine.synthesize("Later", "british_male")
    assert (eleven.calls, edge.calls) == (1, 2)
    now[0] += 3600  # ...a spent allowance only after an hour
    await engine.synthesize("Much later", "british_male")
    assert eleven.calls == 2


async def test_every_voice_down_means_silence_not_a_crash() -> None:
    engine = TtsEngine([FakeProvider("edge", TtsError("offline"))])  # type: ignore[list-item]
    assert await engine.synthesize("Hello", "us_male") is None
    assert engine.active is None


async def test_each_voice_preset_picks_its_own_voice(tmp_path: Path) -> None:
    edge = FakeProvider("edge")
    engine = TtsEngine([edge], tmp_path)  # type: ignore[list-item]
    male = await engine.synthesize("Hi", "british_male")
    female = await engine.synthesize("Hi", "british_female")
    assert male is not None and female is not None
    assert b"en-GB-RyanNeural" in male.data and b"en-GB-SoniaNeural" in female.data


async def test_repeated_phrases_come_from_the_disk_cache(tmp_path: Path) -> None:
    edge = FakeProvider("edge")
    engine = TtsEngine([edge], tmp_path)  # type: ignore[list-item]
    await engine.synthesize("Done.", "us_female")
    await engine.synthesize("Done.", "us_female")
    assert edge.calls == 1


def test_elevenlabs_budget_survives_restarts_and_resets_monthly(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    clock = [datetime(2026, 10, 3, tzinfo=UTC)]
    ledger = UsageLedger(path, lambda: clock[0])
    ledger.add(400)
    assert UsageLedger(path, lambda: clock[0]).remaining(1000) == 600
    clock[0] = datetime(2026, 11, 1, tzinfo=UTC)
    assert UsageLedger(path, lambda: clock[0]).remaining(1000) == 1000


async def test_elevenlabs_stops_before_the_free_allowance_runs_out() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, content=b"mp3-bytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ElevenLabsProvider("key", UsageLedger(None), monthly_chars=30, client=client)
    voice = VoiceSpec("v", "edge", "voice123", "Male", "en-GB")
    speech = await provider.synthesize("Now playing it.", voice)  # 15 chars
    assert speech.data == b"mp3-bytes" and sent[0].url.path.endswith("/voice123")
    assert sent[0].headers["xi-api-key"] == "key"
    await provider.synthesize("Now playing it.", voice)  # 30 of 30 used
    with pytest.raises(TtsQuotaError):
        await provider.synthesize("One more please", voice)
    assert len(sent) == 2


async def test_elevenlabs_rejection_is_a_quota_error_not_a_crash() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    provider = ElevenLabsProvider("bad", UsageLedger(None), client=client)
    with pytest.raises(TtsQuotaError):
        await provider.synthesize("Hello", VoiceSpec("v", "e", "x", "Male", "en-GB"))


# ------------------------------------------------------------------ service


def service(
    tmp_path: Path, provider: FakeProvider | None = None
) -> tuple[VoiceService, NullPlayer, list[VoiceState]]:
    player = NullPlayer()
    changes: list[VoiceState] = []
    engine = TtsEngine([provider or FakeProvider("edge")])  # type: ignore[list-item]
    svc = VoiceService(
        VoicePrefsStore(tmp_path / "voice.json"),
        engine,
        player,
        phraser=phraser(),
        on_change=changes.append,
    )
    return svc, player, changes


async def drain(svc: VoiceService) -> None:
    if svc._task is not None:
        await svc._task


async def test_on_by_default_and_voice_commands_are_answered(tmp_path: Path) -> None:
    svc, player, _ = service(tmp_path)
    assert svc.state.enabled is True and svc.state.style == "jarvis"
    svc.announce(kind="voice", plan=plan_text("pause"), ok=True, message="x")
    await drain(svc)
    assert len(player.played) == 1 and svc.spoken


async def test_typed_commands_are_never_spoken(tmp_path: Path) -> None:
    svc, player, _ = service(tmp_path)
    svc.announce(kind="text", plan=plan_text("pause"), ok=True, message="x")
    assert svc._task is None and not player.played


async def test_button_taps_are_quiet_unless_asked(tmp_path: Path) -> None:
    svc, player, _ = service(tmp_path)
    svc.announce(kind="button", plan=plan_text("pause"), ok=True, message="x")
    assert svc._task is None
    svc.apply({"buttons": True})
    await drain(svc)
    player.played.clear()
    svc.announce(kind="button", plan=plan_text("pause"), ok=True, message="x")
    await drain(svc)
    assert len(player.played) == 1


async def test_preferences_are_remembered_per_tv(tmp_path: Path) -> None:
    svc, _, changes = service(tmp_path)
    assert svc.apply({"enabled": False}).ok
    assert svc.apply({"style": "brief"}).ok and svc.apply({"voice": "us_female"}).ok
    assert changes[-1].style == "brief" and changes[-1].voice == "us_female"

    reborn, _, _ = service(tmp_path)  # a restart of the daemon on the same PC
    assert reborn.state.enabled is False
    assert (reborn.state.style, reborn.state.voice) == ("brief", "us_female")
    assert "engine" not in (tmp_path / "voice.json").read_text()


async def test_off_means_silent_and_a_broken_file_means_defaults(tmp_path: Path) -> None:
    svc, player, _ = service(tmp_path)
    svc.apply({"enabled": False})
    svc.announce(kind="voice", plan=plan_text("pause"), ok=True, message="x")
    assert svc._task is None or svc._task.done()
    (tmp_path / "voice.json").write_text("{not json", encoding="utf-8")
    assert service(tmp_path)[0].state.enabled is True


async def test_changing_style_or_voice_previews_it_but_turning_off_does_not(
    tmp_path: Path,
) -> None:
    svc, player, _ = service(tmp_path)
    svc.apply({"style": "friendly"})
    await drain(svc)
    assert svc.spoken[-1].startswith("You got it")
    spoken = len(svc.spoken)
    svc.apply({"enabled": False})
    await asyncio.sleep(0)
    assert len(svc.spoken) == spoken
    svc.apply({"test": True})  # testing works even while off
    await drain(svc)
    assert len(svc.spoken) == spoken + 1


async def test_bad_values_are_refused(tmp_path: Path) -> None:
    svc, _, _ = service(tmp_path)
    assert not svc.apply({"style": "pirate"}).ok
    assert not svc.apply({"voice": "robot"}).ok
    assert svc.state.style == "jarvis"


async def test_a_new_reply_cuts_off_the_old_one(tmp_path: Path) -> None:
    started = asyncio.Event()

    class SlowPlayer(NullPlayer):
        stopped = 0

        async def play(self, path: Path) -> None:
            started.set()
            await asyncio.sleep(30)

        def stop(self) -> None:
            self.stopped += 1

    player = SlowPlayer()
    svc = VoiceService(
        VoicePrefsStore(None),
        TtsEngine([FakeProvider("edge")]),  # type: ignore[list-item]
        player,
    )
    svc.speak("first")
    await asyncio.wait_for(started.wait(), 2)
    first = svc._task
    svc.speak("second")
    assert player.stopped == 1
    assert first is not None
    await asyncio.gather(first, return_exceptions=True)
    assert first.cancelled()
    await svc.aclose()


# ------------------------------------------------------------------ in the agent


def request(text: str, kind: str = "voice") -> CommandRequest:
    return CommandRequest(
        command_id=f"c-{text}",
        idempotency_key=f"k-{text}",
        device_id="d",
        kind=kind,
        text=text,  # type: ignore[arg-type]
    )


async def test_the_agent_speaks_after_a_command_and_after_a_failure(tmp_path: Path) -> None:
    env = make_env()
    svc, player, _ = service(tmp_path)
    env.deps.voice = svc
    ok = await env.runner().run(request("play the weeknd on youtube music"))
    await drain(svc)
    assert ok.ok and "the weeknd" in svc.spoken[-1].lower()

    env.browser.snap = env.browser.snap.model_copy(update={"url": "https://example.com/"})
    env.router.replies.append("not json")
    env.router.replies.append("still not json")
    bad = await env.runner().run(request("blorp the frobnicator"))
    await drain(svc)
    assert not bad.ok and "didn't catch" in svc.spoken[-1].lower() or "catch" in svc.spoken[-1]
    assert len(player.played) == 2


async def test_say_stop_talking_through_the_agent(tmp_path: Path) -> None:
    env = make_env()
    svc, _, changes = service(tmp_path)
    env.deps.voice = svc
    result = await env.runner().run(request("stop talking"))
    assert result.ok and result.message == "Voice replies off"
    assert changes[-1].enabled is False
