"""Text to speech with automatic fallback: ElevenLabs, then Edge neural voices, then Windows.

Order, best sound first and staying free:
  1. ElevenLabs   - the most natural voices; its free plan is ~10k credits a month, so the daemon
                    keeps its own monthly character budget and moves on before running dry.
  2. Edge neural  - Microsoft's online neural voices through the ``edge-tts`` package: no key, no
                    quota, very natural (British "Ryan" makes a good butler).
  3. Windows      - the voices built into Windows (System.Speech): works with no internet at all.
A provider that fails is skipped for a while (an hour for a key or quota problem, a minute for a
network blip) so a dead service never delays a reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import httpx

log = logging.getLogger(__name__)

Engine = Literal["elevenlabs", "edge", "windows"]
MAX_SPOKEN_CHARS = 280
QUOTA_COOLDOWN_S = 3600.0
BLIP_COOLDOWN_S = 60.0
CACHE_LIMIT = 200


@dataclass(frozen=True)
class VoiceSpec:
    label: str
    edge: str  # Microsoft Edge neural voice name
    eleven: str  # ElevenLabs premade voice id
    windows_gender: Literal["Male", "Female"]
    windows_culture: str


VOICES: dict[str, VoiceSpec] = {
    "british_male": VoiceSpec(
        "Sterling (British, male)", "en-GB-RyanNeural", "JBFqnCBsd6RMkjVDRZzb", "Male", "en-GB"
    ),
    "british_female": VoiceSpec(
        "Aurora (British, female)", "en-GB-SoniaNeural", "Xb7hH8MSUJpSbSDYk0k2", "Female", "en-GB"
    ),
    "us_male": VoiceSpec(
        "Chill (American, male)", "en-US-GuyNeural", "nPczCjzI2devNBz1zQrb", "Male", "en-US"
    ),
    "us_female": VoiceSpec(
        "Aria (American, female)", "en-US-AriaNeural", "EXAVITQu4vr4xnSDxMaL", "Female", "en-US"
    ),
}


@dataclass(frozen=True)
class Speech:
    data: bytes
    suffix: str  # ".mp3" or ".wav"
    engine: Engine


class TtsError(Exception):
    """This provider could not speak right now."""


class TtsQuotaError(TtsError):
    """The key is wrong or the free allowance is used up: do not retry soon."""


class TtsProvider(Protocol):
    name: Engine

    async def synthesize(self, text: str, voice: VoiceSpec) -> Speech: ...


class UsageLedger:
    """Characters sent to ElevenLabs this month, remembered across restarts."""

    def __init__(self, path: Path | None, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._path, self._now = path, now
        self._month, self.chars = self._load()

    def _stamp(self) -> str:
        return self._now().strftime("%Y-%m")

    def _load(self) -> tuple[str, int]:
        if self._path is not None:
            with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if raw["month"] == self._stamp():
                    return str(raw["month"]), int(raw["chars"])
        return self._stamp(), 0

    def remaining(self, limit: int) -> int:
        if self._month != self._stamp():  # a new month starts a fresh allowance
            self._month, self.chars = self._stamp(), 0
        return max(0, limit - self.chars)

    def add(self, count: int) -> None:
        self.remaining(0)  # roll the month over first
        self.chars += count
        if self._path is None:
            return
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"month": self._month, "chars": self.chars}), encoding="utf-8"
            )


class ElevenLabsProvider:
    name: Engine = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        ledger: UsageLedger,
        *,
        monthly_chars: int = 9000,
        model: str = "eleven_flash_v2_5",  # half-price per character, plenty for short replies
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._key, self._ledger, self._limit, self._model = api_key, ledger, monthly_chars, model
        self._client = client or httpx.AsyncClient(timeout=8.0)

    async def synthesize(self, text: str, voice: VoiceSpec) -> Speech:
        if self._ledger.remaining(self._limit) < len(text):
            raise TtsQuotaError("monthly ElevenLabs budget used up")
        try:
            resp = await self._client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice.eleven}",
                params={"output_format": "mp3_44100_64"},
                headers={"xi-api-key": self._key},
                json={"text": text, "model_id": self._model},
            )
        except httpx.HTTPError as exc:
            raise TtsError(f"ElevenLabs unreachable: {type(exc).__name__}") from exc
        if resp.status_code in (401, 402, 403, 429):
            raise TtsQuotaError(f"ElevenLabs refused the request ({resp.status_code})")
        if resp.status_code != 200 or not resp.content:
            raise TtsError(f"ElevenLabs returned {resp.status_code}")
        self._ledger.add(len(text))
        return Speech(resp.content, ".mp3", "elevenlabs")


class EdgeProvider:
    name: Engine = "edge"

    def __init__(self, *, timeout_s: float = 8.0) -> None:
        self._timeout = timeout_s

    async def synthesize(self, text: str, voice: VoiceSpec) -> Speech:
        try:
            import edge_tts
        except ImportError as exc:
            raise TtsQuotaError("edge-tts is not installed") from exc
        audio = bytearray()
        try:
            async with asyncio.timeout(self._timeout):
                async for chunk in edge_tts.Communicate(text, voice.edge).stream():
                    if chunk["type"] == "audio":
                        audio += chunk["data"]
        except Exception as exc:  # timeout, network, DNS, a changed service...
            raise TtsError(f"Edge voices unavailable: {type(exc).__name__}") from exc
        if not audio:
            raise TtsError("Edge voices returned no audio")
        return Speech(bytes(audio), ".mp3", "edge")


_WINDOWS_SCRIPT = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $culture = New-Object System.Globalization.CultureInfo($env:CJ_TTS_CULTURE)
  $gender = [System.Enum]::Parse([System.Speech.Synthesis.VoiceGender], $env:CJ_TTS_GENDER)
  $s.SelectVoiceByHints($gender, 'NotSet', 0, $culture)
} catch {}
$s.SetOutputToWaveFile($env:CJ_TTS_OUT)
$s.Speak($env:CJ_TTS_TEXT)
$s.Dispose()
"""


def _read_and_remove(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    finally:
        with contextlib.suppress(OSError):
            Path(path).unlink()


class WindowsProvider:
    """The voices that ship with Windows. The text travels in the environment, not the command."""

    name: Engine = "windows"

    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._timeout = timeout_s

    async def synthesize(self, text: str, voice: VoiceSpec) -> Speech:
        if sys.platform != "win32":
            raise TtsQuotaError("Windows voices need Windows")
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="cj-tts-")
        os.close(fd)
        env = {
            **os.environ,
            "CJ_TTS_TEXT": text,
            "CJ_TTS_OUT": out,
            "CJ_TTS_GENDER": voice.windows_gender,
            "CJ_TTS_CULTURE": voice.windows_culture,
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )  # fmt: skip
            try:
                async with asyncio.timeout(self._timeout):
                    await proc.wait()
            except TimeoutError:
                proc.kill()
                raise TtsError("Windows voice timed out") from None
            data = await asyncio.to_thread(_read_and_remove, out)
        except OSError as exc:
            raise TtsError(f"Windows voice failed: {exc}") from exc
        if len(data) < 100:
            raise TtsError("Windows voice produced no audio")
        return Speech(data, ".wav", "windows")


class TtsEngine:
    """Tries the providers in order; remembers which are down; caches short phrases on disk."""

    def __init__(
        self,
        providers: list[TtsProvider],
        cache_dir: Path | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._cache = cache_dir
        self._clock = clock
        self._down_until: dict[str, float] = {}

    def _usable(self, provider: TtsProvider) -> bool:
        return self._clock() >= self._down_until.get(provider.name, 0.0)

    @property
    def active(self) -> Engine | None:
        """The engine the next reply will use, as far as we know."""
        return next((p.name for p in self._providers if self._usable(p)), None)

    def _cache_path(self, provider: str, voice_id: str, text: str, suffix: str) -> Path | None:
        if self._cache is None:
            return None
        digest = hashlib.sha1(f"{provider}|{voice_id}|{text}".encode()).hexdigest()
        return self._cache / f"{digest}{suffix}"

    def _from_cache(self, provider: TtsProvider, voice_id: str, text: str) -> Speech | None:
        for suffix in (".mp3", ".wav"):
            path = self._cache_path(provider.name, voice_id, text, suffix)
            if path is not None and path.exists():
                with contextlib.suppress(OSError):
                    return Speech(path.read_bytes(), suffix, provider.name)
        return None

    def _remember(self, speech: Speech, voice_id: str, text: str) -> None:
        path = self._cache_path(speech.engine, voice_id, text, speech.suffix)
        if path is None:
            return
        with contextlib.suppress(OSError):
            assert self._cache is not None
            self._cache.mkdir(parents=True, exist_ok=True)
            path.write_bytes(speech.data)
            files = sorted(self._cache.iterdir(), key=lambda f: f.stat().st_mtime)
            for stale in files[:-CACHE_LIMIT]:
                stale.unlink()

    async def synthesize(self, text: str, voice_id: str) -> Speech | None:
        text = " ".join(text.split())[:MAX_SPOKEN_CHARS]
        if not text:
            return None
        voice = VOICES.get(voice_id, VOICES["british_male"])
        for provider in self._providers:
            if not self._usable(provider):
                continue
            cached = self._from_cache(provider, voice_id, text)
            if cached is not None:
                return cached
            try:
                speech = await provider.synthesize(text, voice)
            except TtsQuotaError as exc:
                log.warning("tts %s is switched off for a while: %s", provider.name, exc)
                self._down_until[provider.name] = self._clock() + QUOTA_COOLDOWN_S
            except TtsError as exc:
                log.info("tts %s failed, trying the next voice: %s", provider.name, exc)
                self._down_until[provider.name] = self._clock() + BLIP_COOLDOWN_S
            else:
                self._remember(speech, voice_id, text)
                return speech
        return None
