"""The language model: kinds of failure, hardware-aware defaults, starting Ollama for the user."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import hardware
from brain import prepare_ollama
from llm.base import LLMError, LLMReply, brain_message
from llm.fallback import FallbackLLM
from llm.gemini import GeminiClient
from llm.ollama import OllamaClient


def ollama(handler: httpx.MockTransport) -> OllamaClient:
    return OllamaClient("http://o:11434", "m", client=httpx.AsyncClient(transport=handler))


async def kind_of(response: httpx.Response | Exception) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    with pytest.raises(LLMError) as err:
        await ollama(httpx.MockTransport(handler)).complete("s", "u")
    return err.value.kind


async def test_each_failure_has_its_own_kind() -> None:
    assert await kind_of(httpx.ConnectError("refused")) == "unavailable"
    assert await kind_of(httpx.ReadTimeout("slow")) == "timeout"
    assert await kind_of(httpx.Response(404)) == "model_missing"
    assert await kind_of(httpx.Response(200, content=b"nope")) == "bad_reply"


def test_every_kind_has_a_message_that_says_what_to_do() -> None:
    assert "Set up AI brain" in brain_message(LLMError("x", "unavailable"))
    assert "Set up AI brain" in brain_message(LLMError("x", "model_missing"))
    assert "too long" in brain_message(LLMError("x", "timeout"))
    assert "Gemini key" in brain_message(LLMError("x", "bad_key"))
    assert brain_message(RuntimeError("unknown"))  # never empty


async def test_gemini_key_rejected_is_reported_as_such() -> None:
    client = GeminiClient(
        "k", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    )
    with pytest.raises(LLMError) as err:
        await client.complete("s", "u")
    assert err.value.kind == "bad_key"


class Stub:
    def __init__(self, text: str | None) -> None:
        self.text, self.calls = text, 0

    async def complete(self, system: str, user: str) -> LLMReply:
        self.calls += 1
        if self.text is None:
            raise LLMError("down", "timeout")
        return LLMReply(self.text, "stub", "m")


async def test_fallback_uses_the_next_provider_and_stops_at_the_first_answer() -> None:
    bad, good, spare = Stub(None), Stub("ok"), Stub("unused")
    reply = await FallbackLLM([bad, good, spare]).complete("s", "u")  # type: ignore[list-item]
    assert reply.text == "ok" and (bad.calls, good.calls, spare.calls) == (1, 1, 0)


async def test_fallback_raises_the_error_the_user_can_act_on() -> None:
    class Failing(Stub):
        def __init__(self, kind: str) -> None:
            super().__init__(None)
            self.kind = kind

        async def complete(self, system: str, user: str) -> LLMReply:
            raise LLMError("down", self.kind)

    with pytest.raises(LLMError) as err:  # rate-limited cloud, no local model installed
        await FallbackLLM([Failing("rate_limited"), Failing("unavailable")]).complete("s", "u")  # type: ignore[list-item]
    assert err.value.kind == "unavailable"


def test_models_fit_the_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "has_nvidia_gpu", lambda: False)
    assert hardware.speech_model("auto", "auto") == "small"
    assert hardware.speech_model("auto", "cuda") == "large-v3-turbo"
    assert hardware.speech_model("base", "cpu") == "base"  # an explicit choice wins
    assert hardware.text_model("auto") == "qwen2.5:3b"
    monkeypatch.setattr(hardware, "has_nvidia_gpu", lambda: True)
    assert hardware.speech_model("auto", "auto") == "large-v3-turbo"
    assert hardware.text_model("auto") == "qwen2.5:7b"
    assert hardware.text_model("llama3.2:3b") == "llama3.2:3b"


class FakeOllamaServer:
    """Answers /api/tags only after `start` was called, like a server that was not running."""

    def __init__(self, models: list[str], running: bool) -> None:
        self.models, self.running, self.preloaded = models, running, False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.running:
            raise httpx.ConnectError("refused")
        if request.url.path == "/api/generate":
            self.preloaded = True
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def test_starts_a_stopped_ollama_then_loads_the_model() -> None:
    server = FakeOllamaServer(["qwen2.5:3b"], running=False)
    started: list[Path] = []

    def start(exe: Path) -> None:
        started.append(exe)
        server.running = True

    status = await prepare_ollama(
        "http://o",
        "qwen2.5:3b",
        client=server.client(),
        finder=lambda: Path("ollama.exe"),
        starter=start,
        poll_s=0.0,
    )
    assert status.state == "ready" and started and server.preloaded


async def test_reports_not_installed_when_there_is_no_ollama() -> None:
    server = FakeOllamaServer([], running=False)
    status = await prepare_ollama(
        "http://o", "m", client=server.client(), finder=lambda: None, poll_s=0.0
    )
    assert status.state == "not_installed" and not status.usable


async def test_reports_a_missing_model() -> None:
    server = FakeOllamaServer(["other:1b"], running=True)
    status = await prepare_ollama("http://o", "qwen2.5:3b", client=server.client())
    assert status.state == "model_missing" and not server.preloaded


async def test_gives_up_when_ollama_never_comes_up() -> None:
    server = FakeOllamaServer([], running=False)
    status = await prepare_ollama(
        "http://o",
        "m",
        client=server.client(),
        finder=lambda: Path("o.exe"),
        starter=lambda exe: None,
        wait_s=2.0,
        poll_s=1.0,
    )
    assert status.state == "not_running"
