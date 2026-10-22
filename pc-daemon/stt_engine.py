"""Speech-to-text: local faster-whisper (CUDA) with a Groq Whisper API fallback."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
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


def cuda_dll_dirs(search_roots: Iterable[Path]) -> list[Path]:
    """Folders holding NVIDIA's CUDA runtime DLLs, as shipped by the ``nvidia-*-cu12`` wheels.

    CTranslate2 needs cuBLAS and cuDNN at inference time but does not ship them. Installing
    ``requirements-gpu.txt`` puts them in site-packages/nvidia/<lib>/bin; Windows only finds
    them if that folder is on the DLL search path.
    """
    found: list[Path] = []
    for root in search_roots:
        for bin_dir in sorted((root / "nvidia").glob("*/bin")):
            if any(bin_dir.glob("*.dll")):
                found.append(bin_dir)
    return found


def register_cuda_dll_dirs() -> list[Path]:
    """Make the pip-installed CUDA DLLs loadable. Safe to call repeatedly; a no-op off Windows."""
    if sys.platform != "win32":
        return []
    roots = [Path(p) for p in sys.path if p]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    dirs = cuda_dll_dirs(roots)
    for d in dirs:
        os.add_dll_directory(str(d))
        if str(d) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    return dirs


class FasterWhisperTranscriber:
    """Local Whisper. If the GPU stack is broken (missing cuBLAS/cuDNN, out of memory, driver
    trouble) it switches to the CPU for good instead of failing every voice command."""

    name = "faster-whisper"

    def __init__(self, model_name: str, device: str = "cuda") -> None:
        self._model_name = model_name
        self._device = device
        self._active_device = ""
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _wanted_device(self) -> str:
        if self._device != "auto":
            return self._device
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"

    def _load(self, device: str) -> Any:
        from faster_whisper import WhisperModel

        if device == "cuda":
            register_cuda_dll_dirs()
        compute_type = "int8_float16" if device == "cuda" else "int8"
        model = WhisperModel(self._model_name, device=device, compute_type=compute_type)
        self._active_device = device
        return model

    def _load_best(self) -> Any:
        device = self._wanted_device()
        try:
            return self._load(device)
        except Exception as exc:
            if device != "cuda":
                raise
            self._warn_gpu(exc)
            return self._load("cpu")

    @staticmethod
    def _warn_gpu(exc: BaseException) -> None:
        log.warning(
            "GPU speech failed (%s); using the CPU instead, which is slower. To use the GPU "
            "install the CUDA libraries: uv pip install -r requirements-gpu.txt",
            exc,
        )

    @staticmethod
    def _run(model: Any, audio: bytes) -> str:
        segments, _info = model.transcribe(
            io.BytesIO(audio), beam_size=1, vad_filter=True, condition_on_previous_text=False
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def warm_up(self) -> None:
        """Load the model now so the first voice command does not pay the ~20 s load."""
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_best)

    async def transcribe(self, audio: bytes, mime: str) -> str:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_best)
            model = self._model
        try:
            return await asyncio.to_thread(self._run, model, audio)
        except Exception as exc:
            if self._active_device != "cuda":
                raise
            # cuBLAS/cuDNN are loaded lazily, so a broken GPU stack often only fails here
            self._warn_gpu(exc)
            async with self._lock:
                if self._active_device == "cuda":
                    self._model = await asyncio.to_thread(self._load, "cpu")
                model = self._model
            return await asyncio.to_thread(self._run, model, audio)


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

    async def warm_up(self) -> None:
        """Preload local models in the background. Never raises: the first command retries."""
        for transcriber in self._chain:
            warm = getattr(transcriber, "warm_up", None)
            if warm is None:
                continue
            try:
                await warm()
            except Exception:
                log.warning("STT warm-up for %s failed", transcriber.name, exc_info=True)

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
