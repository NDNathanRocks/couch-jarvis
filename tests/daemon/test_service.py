from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from contracts import (
    AudioBegin,
    AudioChunk,
    AudioEnd,
    CmdButton,
    CmdText,
    ErrorMsg,
    Ping,
    Presence,
    StatePatch,
    StateSnapshot,
    StateSync,
    envelope,
    parse_message,
)
from event_bus import EventBus
from service import MAX_AUDIO_BYTES, CommandService
from stt_engine import STTEngine, STTError
from support import AgentEnv
from timers import TimerService


class FakeStt:
    name = "faster-whisper"

    def __init__(self, text: str = "pause", error: Exception | None = None) -> None:
        self.text, self.error, self.seen = text, error, []

    async def transcribe(self, audio: bytes, mime: str) -> str:
        self.seen.append((audio, mime))
        if self.error:
            raise self.error
        return self.text


@pytest.fixture
async def svc(env: AgentEnv) -> AsyncIterator[CommandService]:
    bus = EventBus()
    timers = TimerService(lambda info: asyncio.sleep(0))
    env.deps.timers = timers
    service = CommandService(
        room_id="room1",
        bus=bus,
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=timers,
        stt=STTEngine(FakeStt("pause"), None),
        mode="lan",
        state_poll_s=3600,
    )
    await service.start()
    yield service
    await service.stop()


def events(svc: CommandService) -> list[dict[str, Any]]:
    return [json.loads(e.data) for e in svc.bus.replay_since(0)]


def types(svc: CommandService) -> list[str]:
    return [e["type"] for e in events(svc)]


async def settle(svc: CommandService, wanted: str, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while wanted not in types(svc):
            await asyncio.sleep(0.01)


def msg(cls: type, payload: dict[str, Any], sender: str = "d1") -> Any:
    return envelope(cls, "room1", payload, from_=sender)


async def test_button_command_flow_publishes_ack_progress_result(svc: CommandService) -> None:
    payload = {"command_id": "c1", "idempotency_key": "k1", "action": "next"}
    assert await svc.handle_envelope(msg(CmdButton, payload), "d1") == []
    await settle(svc, "cmd.result")
    t = types(svc)
    assert t.index("cmd.ack") < t.index("agent.progress") < t.index("cmd.result")
    result = next(e for e in events(svc) if e["type"] == "cmd.result")
    assert result["payload"]["ok"] is True and result["payload"]["command_id"] == "c1"


async def test_duplicate_press_runs_once_and_replays_the_cached_result(
    svc: CommandService, env: AgentEnv
) -> None:
    payload = {"command_id": "c1", "idempotency_key": "k1", "action": "next"}
    await svc.handle_envelope(msg(CmdButton, payload), "d1")
    await settle(svc, "cmd.result")
    # the same phone retries (flaky network) with the same key
    await svc.handle_envelope(msg(CmdButton, {**payload, "command_id": "c2"}), "d1")
    acks = [e["payload"]["status"] for e in events(svc) if e["type"] == "cmd.ack"]
    assert acks == ["accepted", "duplicate"]
    assert env.browser.calls.count("media:next") == 1
    assert types(svc).count("cmd.result") == 2  # cached result is re-sent for the duplicate


async def test_text_command_goes_through_the_agent(svc: CommandService, env: AgentEnv) -> None:
    payload = {"command_id": "c1", "idempotency_key": "k1", "text": "volume to 20"}
    await svc.handle_envelope(msg(CmdText, payload), "d1")
    await settle(svc, "cmd.result")
    assert env.os.volume == 20


async def test_voice_flow_reassembles_chunks_and_transcribes(
    svc: CommandService, env: AgentEnv
) -> None:
    stt = FakeStt("volume to 35")
    svc._stt = STTEngine(stt, None)
    audio = b"OPUSDATA" * 1000
    pieces = [audio[:4000], audio[4000:8000]]
    base = {"command_id": "v1"}
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}), "d1"
    )
    for seq in (1, 0):  # out-of-order arrival is fine
        b64 = base64.b64encode(pieces[seq]).decode()
        assert (
            await svc.handle_envelope(msg(AudioChunk, {**base, "seq": seq, "b64": b64}), "d1") == []
        )
    await svc.handle_envelope(msg(AudioEnd, {**base, "total_chunks": 2, "duration_ms": 1500}), "d1")
    await settle(svc, "cmd.result")
    assert stt.seen == [(b"".join(pieces), "audio/webm")]
    assert env.os.volume == 35
    progress = [e["payload"]["node"] for e in events(svc) if e["type"] == "agent.progress"]
    assert progress[0] == "stt"
    assert next(e for e in events(svc) if e["type"] == "cmd.result")["payload"]["ok"] is True


async def test_incomplete_audio_is_rejected_cleanly(
    svc: CommandService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("service.AUDIO_GRACE_S", 0.05)
    base = {"command_id": "v1"}
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}), "d1"
    )
    await svc.handle_envelope(msg(AudioChunk, {**base, "seq": 0, "b64": "AAAA"}), "d1")
    await svc.handle_envelope(msg(AudioEnd, {**base, "total_chunks": 3, "duration_ms": 10}), "d1")
    await settle(svc, "cmd.result")  # only after the grace period for late chunks
    result = next(e for e in events(svc) if e["type"] == "cmd.result")
    assert result["payload"]["ok"] is False and "incomplete" in result["payload"]["message"]


@pytest.mark.parametrize(
    "order",
    [
        ["end", 0, 1, "begin"],  # the relay runs each message in its own Lambda: any order
        ["end", "begin", 1, 0],
        [0, "begin", "end", 1],
        [1, 0, "end", "begin"],
        ["begin", 0, 1, "end"],
    ],
)
async def test_voice_survives_any_arrival_order(
    svc: CommandService, env: AgentEnv, order: list
) -> None:
    svc._stt = STTEngine(FakeStt("volume to 35"), None)
    pieces = [b"OPUS" * 500, b"DATA" * 500]
    base = {"command_id": "v1"}
    steps = {
        "begin": msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}),
        "end": msg(AudioEnd, {**base, "total_chunks": 2, "duration_ms": 1500}),
        0: msg(AudioChunk, {**base, "seq": 0, "b64": base64.b64encode(pieces[0]).decode()}),
        1: msg(AudioChunk, {**base, "seq": 1, "b64": base64.b64encode(pieces[1]).decode()}),
    }
    for step in order:
        assert await svc.handle_envelope(steps[step], "d1") == []  # no error for early arrivals
    await settle(svc, "cmd.result")
    assert env.os.volume == 35
    assert next(e for e in events(svc) if e["type"] == "cmd.result")["payload"]["ok"] is True


async def test_audio_cannot_be_hijacked_by_another_device(svc: CommandService) -> None:
    svc._stt = STTEngine(FakeStt("volume to 35"), None)
    base = {"command_id": "v1"}
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}), "d1"
    )
    await svc.handle_envelope(msg(AudioChunk, {**base, "seq": 0, "b64": "AAAA"}, "d2"), "d2")
    # d2's bytes live in d2's own buffer; d1's recording never sees them
    assert svc._audio[("d1", "v1")].chunks == {}
    assert svc._audio[("d2", "v1")].chunks == {0: b"\x00\x00\x00"}


async def test_oversized_and_corrupt_audio_are_refused(svc: CommandService) -> None:
    base = {"command_id": "v1"}
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}), "d1"
    )
    bad = await svc.handle_envelope(
        msg(AudioChunk, {**base, "seq": 0, "b64": "!!not base64!!"}), "d1"
    )
    assert bad[0].payload.code == "bad_audio"  # type: ignore[union-attr]
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv2", "mime": "audio/webm"}), "d1"
    )
    chunk = base64.b64encode(b"x" * 18000).decode()
    codes: list[str] = []
    for seq in range(MAX_AUDIO_BYTES // 18000 + 2):
        out = await svc.handle_envelope(msg(AudioChunk, {**base, "seq": seq, "b64": chunk}), "d1")
        codes += [m.payload.code for m in out if isinstance(m, ErrorMsg)]
    assert "too_large" in codes


async def test_stt_failure_reports_to_phones(svc: CommandService) -> None:
    svc._stt = STTEngine(FakeStt(error=STTError("down")), None)
    base = {"command_id": "v1"}
    await svc.handle_envelope(
        msg(AudioBegin, {**base, "idempotency_key": "kv", "mime": "audio/webm"}), "d1"
    )
    await svc.handle_envelope(msg(AudioChunk, {**base, "seq": 0, "b64": "AAAA"}), "d1")
    await svc.handle_envelope(msg(AudioEnd, {**base, "total_chunks": 1, "duration_ms": 10}), "d1")
    await settle(svc, "cmd.result")
    result = next(e for e in events(svc) if e["type"] == "cmd.result")
    assert result["payload"]["ok"] is False and "hear" in result["payload"]["message"]


async def test_voice_unavailable_without_stt(svc: CommandService) -> None:
    svc._stt = None
    reply = await svc.handle_envelope(
        msg(AudioBegin, {"command_id": "v", "idempotency_key": "k", "mime": "audio/webm"}), "d1"
    )
    assert reply[0].payload.code == "stt_unavailable"  # type: ignore[union-attr]


async def test_ping_sync_and_forbidden_types(svc: CommandService) -> None:
    ping = msg(Ping, {})
    (pong,) = await svc.handle_envelope(ping, "d1")
    assert pong.type == "pong" and pong.reply_to == ping.id
    (snap,) = await svc.handle_envelope(msg(StateSync, {}), "d1")
    assert isinstance(snap, StateSnapshot) and snap.payload.pc_online is True
    for forbidden in (msg(StatePatch, {"volume": 100, "rev": 99}), msg(Presence, {"devices": []})):
        (err,) = await svc.handle_envelope(forbidden, "d1")
        assert isinstance(err, ErrorMsg) and err.payload.code == "forbidden_type"
    assert svc.state.state.volume == 50  # a phone cannot write room state


async def test_state_patches_are_minimal_and_revisioned(svc: CommandService) -> None:
    before = svc.state.state.rev
    assert svc.state.update(volume=70) is True
    assert svc.state.update(volume=70) is False  # no change, no patch
    patches = [e for e in events(svc) if e["type"] == "state.patch"]
    assert patches[-1]["payload"] == {"volume": 70, "rev": before + 1}
    assert svc.state.state.volume == 70


async def test_presence_and_queue_state_are_tracked(svc: CommandService) -> None:
    svc.device_connected("d1", "Pixel")
    assert [d.name for d in svc.state.state.devices] == ["Pixel"] and svc.state.state.devices[
        0
    ].online
    svc.device_disconnected("d1")
    assert svc.state.state.devices[0].online is False
    await svc.handle_envelope(
        msg(CmdText, {"command_id": "c", "idempotency_key": "k", "text": "pause"}), "d1"
    )
    await settle(svc, "cmd.result")
    await asyncio.sleep(0.05)
    assert svc.state.state.queue_depth == 0 and svc.state.state.driver is None


async def test_state_reflects_the_tv_after_a_command(svc: CommandService, env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(
        update={"title": "Lofi Girl", "is_playing": True}
    )
    await svc.handle_envelope(
        msg(CmdText, {"command_id": "c", "idempotency_key": "k", "text": "volume to 25"}), "d1"
    )
    await settle(svc, "cmd.result")
    await asyncio.sleep(0.05)
    st = svc.state.state
    assert st.volume == 25 and st.now_playing is not None and st.now_playing.title == "Lofi Girl"


async def test_pc_is_kept_awake_only_while_media_plays(svc: CommandService, env: AgentEnv) -> None:
    env.browser.snap = env.browser.snap.model_copy(update={"title": "Film", "is_playing": True})
    await svc.refresh_state()
    assert env.os.awake is True
    env.browser.snap = env.browser.snap.model_copy(update={"is_playing": False})
    await svc.refresh_state()
    assert env.os.awake is False  # paused video lets the PC sleep again
    env.browser.snap = env.browser.snap.model_copy(update={"is_playing": True})
    await svc.refresh_state()
    await svc.stop()
    assert env.os.awake is False  # and shutting down never leaves it pinned awake


def test_parse_roundtrip_of_published_events(svc: CommandService) -> None:
    svc.state.update(muted=True)
    for e in svc.bus.replay_since(0):
        parse_message(e.data)  # everything the bus emits is a valid contract message


def slider(action: str, **args: Any) -> Any:
    payload = {"command_id": "s1", "idempotency_key": "sk1", "action": action, "args": args}
    return msg(CmdButton, payload)


async def test_volume_slider_applies_at_once_without_a_reply_or_a_queued_command(
    svc: CommandService, env: AgentEnv
) -> None:
    assert await svc.handle_envelope(slider("volume_set", level=37), "d1") == []
    assert env.os.volume == 37 and svc.state.state.volume == 37
    assert "cmd.ack" not in types(svc) and "cmd.result" not in types(svc)


async def test_timeline_slider_seeks_to_an_absolute_time(
    svc: CommandService, env: AgentEnv
) -> None:
    assert await svc.handle_envelope(slider("seek_to", position_s=125.5), "d1") == []
    assert "seek_to:125.5" in env.browser.calls


async def test_seeking_with_nothing_to_seek_in_says_so(svc: CommandService, env: AgentEnv) -> None:
    env.browser.can_seek = False
    replies = await svc.handle_envelope(slider("seek_to", position_s=5), "d1")
    assert [r.payload.code for r in replies if isinstance(r, ErrorMsg)] == ["seek_failed"]


async def test_a_drag_is_rate_limited_per_phone(svc: CommandService) -> None:
    codes: list[str] = []
    for level in range(100):  # far faster than a thumb can move
        for r in await svc.handle_envelope(slider("volume_set", level=level % 100), "d1"):
            codes.append(r.payload.code)
    assert "rate_limited" in codes
    assert await svc.handle_envelope(slider("volume_set", level=10), "other-phone") == []


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "volume_set", "args": {}},
        {"action": "volume_set", "args": {"level": 101}},
        {"action": "volume_set", "args": {"level": -1}},
        {"action": "volume_set", "args": {"level": True}},
        {"action": "volume_set", "args": {"level": 5, "extra": "x"}},
        {"action": "seek_to", "args": {"position_s": -3}},
        {"action": "seek_to", "args": {"position_s": "10"}},
        {"action": "seek_to", "args": {"level": 10}},
    ],
)
def test_slider_values_are_validated_by_the_contract(payload: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from contracts import CmdButtonPayload

    with pytest.raises(ValidationError):
        CmdButtonPayload(command_id="c", idempotency_key="k", **payload)


async def test_room_state_advertises_the_daemon_version(svc: CommandService) -> None:
    from version import __version__

    assert svc.state.state.daemon_version == __version__
