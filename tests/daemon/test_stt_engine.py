from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from stt_engine import GroqTranscriber, STTEngine, STTError
from telemetry import Telemetry


class FakeLocal:
    name = "faster-whisper"

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text, self.error, self.calls = text, error, 0

    async def transcribe(self, audio: bytes, mime: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.text


class FakeGroq(FakeLocal):
    name = "groq"


def spans(directory: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for p in directory.glob("*.jsonl"):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    return out


async def test_local_success_does_not_touch_groq(telemetry: Telemetry, telemetry_dir: Path) -> None:
    groq = FakeGroq("never")
    engine = STTEngine(FakeLocal("play lofi"), groq, telemetry)
    result = await engine.transcribe(b"audio", "audio/webm", audio_ms=3100)
    assert (result.text, result.engine) == ("play lofi", "faster-whisper")
    assert groq.calls == 0
    (rec,) = spans(telemetry_dir)
    assert rec["attributes"]["cj.stt_engine"] == "faster-whisper"  # type: ignore[index]
    assert rec["attributes"]["cj.stt_audio_ms"] == 3100  # type: ignore[index]


async def test_cuda_oom_falls_back_to_groq_and_is_recorded(
    telemetry: Telemetry, telemetry_dir: Path
) -> None:
    local = FakeLocal(error=RuntimeError("CUDA out of memory"))
    engine = STTEngine(local, FakeGroq("pause the video"), telemetry)
    result = await engine.transcribe(b"audio", "audio/webm")
    assert (result.text, result.engine) == ("pause the video", "groq")
    (rec,) = spans(telemetry_dir)
    attrs = rec["attributes"]
    assert attrs["cj.stt_engine"] == "groq"  # type: ignore[index]
    assert attrs["gen_ai.system"] == "groq"  # type: ignore[index]
    assert attrs["cj.stt_fallback_reason"] == "faster-whisper: RuntimeError"  # type: ignore[index]


async def test_all_engines_failing_raises_and_marks_span(
    telemetry: Telemetry, telemetry_dir: Path
) -> None:
    engine = STTEngine(FakeLocal(error=RuntimeError("x")), FakeGroq(error=STTError("y")), telemetry)
    with pytest.raises(STTError):
        await engine.transcribe(b"a", "audio/webm")
    assert spans(telemetry_dir)[0]["status"] == "ERROR"


async def test_no_transcribers_configured() -> None:
    with pytest.raises(STTError):
        await STTEngine(None, None).transcribe(b"a", "audio/webm")


async def test_empty_local_transcript_is_not_a_failure() -> None:
    groq = FakeGroq("x")
    result = await STTEngine(FakeLocal(""), groq).transcribe(b"a", "audio/webm")
    assert result.text == "" and groq.calls == 0


# ----------------------------------------------- GroqTranscriber over a mock transport


def groq_with(handler: httpx.MockTransport, sleeps: list[float]) -> GroqTranscriber:
    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    return GroqTranscriber(
        "gsk_test", client=httpx.AsyncClient(transport=handler), sleep=fake_sleep
    )


async def test_groq_request_shape_and_parse() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " hello world "})

    g = groq_with(httpx.MockTransport(handler), [])
    assert await g.transcribe(b"\x1a\x45", "audio/webm;codecs=opus") == "hello world"
    assert seen["auth"] == "Bearer gsk_test"
    body = seen["body"]
    assert isinstance(body, bytes) and b"whisper-large-v3-turbo" in body and b"audio.webm" in body


async def test_groq_retries_429_honoring_retry_after() -> None:
    calls = [0]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        if calls[0] < 3:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"text": "ok"})

    g = groq_with(httpx.MockTransport(handler), sleeps)
    assert await g.transcribe(b"a", "audio/webm") == "ok"
    assert calls[0] == 3 and sleeps == [2.0, 2.0]


async def test_groq_gives_up_after_max_attempts() -> None:
    sleeps: list[float] = []
    g = groq_with(httpx.MockTransport(lambda r: httpx.Response(503)), sleeps)
    with pytest.raises(STTError, match="HTTP 503"):
        await g.transcribe(b"a", "audio/webm")
    assert len(sleeps) == 3


async def test_groq_does_not_retry_client_errors() -> None:
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(401)

    g = groq_with(httpx.MockTransport(handler), [])
    with pytest.raises(STTError, match="HTTP 401"):
        await g.transcribe(b"a", "audio/webm")
    assert calls[0] == 1
