"""Speech-to-text: local faster-whisper (CUDA) with a Groq Whisper API fallback."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from config import Settings
from telemetry import Telemetry

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class STTError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    engine: str


class Transcriber(Protocol):
    name: str

    async def transcribe(self, audio: bytes, mime: str) -> str: ...


def _extension(mime: str) -> str:
    mime = mime.lower()
    for needle, ext in (("webm", ".webm"), ("mp4", ".m4a"), ("ogg", ".ogg"), ("wav", ".wav")):
        if needle in mime:
            return ext
    return ".bin"


class FasterWhisperTranscriber:
    name = "faster-whisper"

    def __init__(self, model_name: str, device: str = "cuda") -> None:
        self._model_name = model_name
        self._device = device
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        device = self._device
        if device == "auto":
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "int8_float16" if device == "cuda" else "int8"
        return WhisperModel(self._model_name, device=device, compute_type=compute_type)

    async def transcribe(self, audio: bytes, mime: str) -> str:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
            model = self._model

        def run() -> str:
            segments, _info = model.transcribe(
                io.BytesIO(audio), beam_size=1, vad_filter=True, condition_on_previous_text=False
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        return await asyncio.to_thread(run)


class GroqTranscriber:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-large-v3-turbo",
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def transcribe(self, audio: bytes, mime: str) -> str:
        files = {"file": (f"audio{_extension(mime)}", audio, mime)}
        data = {"model": self._model, "response_format": "json", "temperature": "0"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error = "no attempts made"
        for attempt in range(self._max_attempts):
            try:
                resp = await self._client.post(GROQ_URL, files=files, data=data, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"network error: {type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    return str(resp.json().get("text", "")).strip()
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in (429, 500, 502, 503, 504):
                    break  # client error: retrying will not help
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    await self._sleep(min(float(retry_after), 5.0))
                    continue
            await self._sleep(0.5 * 2**attempt)
        raise STTError(f"groq transcription failed: {last_error}")


class STTEngine:
    """Tries each transcriber in order; the first one that does not raise wins."""

    def __init__(
        self,
        primary: Transcriber | None,
        fallback: Transcriber | None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._chain = [t for t in (primary, fallback) if t is not None]
        self._telemetry = telemetry

    async def transcribe(
        self, audio: bytes, mime: str, *, audio_ms: int | None = None
    ) -> Transcript:
        if not self._chain:
            raise STTError("no transcribers configured")
        telemetry = self._telemetry or Telemetry(None)
        with telemetry.span("stt.transcribe") as span:
            span.set_attribute("cj.stt_audio_ms", audio_ms)
            errors: list[str] = []
            for transcriber in self._chain:
                try:
                    text = await transcriber.transcribe(audio, mime)
                except Exception as exc:
                    log.warning("STT engine %s failed: %r", transcriber.name, exc)
                    errors.append(f"{transcriber.name}: {type(exc).__name__}")
                    span.set_attribute("cj.stt_fallback_reason", errors[-1])
                    continue
                span.set_attribute("cj.stt_engine", transcriber.name)
                if transcriber.name == "groq":
                    span.set_attribute("gen_ai.system", "groq")
                return Transcript(text=text, engine=transcriber.name)
            err = STTError("all STT engines failed: " + "; ".join(errors))
            span.set_error(err)
            raise err


def build_stt_engine(settings: Settings, telemetry: Telemetry) -> STTEngine:
    primary = FasterWhisperTranscriber(settings.stt_model, settings.stt_device)
    fallback = (
        GroqTranscriber(settings.groq_api_key.get_secret_value())
        if settings.groq_api_key is not None
        else None
    )
    return STTEngine(primary, fallback, telemetry)
