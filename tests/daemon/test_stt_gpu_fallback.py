"""A broken GPU stack must degrade to the CPU, not fail every voice command."""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from stt_engine import FasterWhisperTranscriber, cuda_dll_dirs

CUBLAS_ERROR = "Library cublas64_12.dll is not found or cannot be loaded"


class FakeFasterWhisper:
    """Stands in for the faster_whisper module; records every model it is asked to build."""

    def __init__(self, *, fail_load_on: str | None = None, fail_run_on: str | None = None) -> None:
        self.built: list[str] = []
        self.fail_load_on, self.fail_run_on = fail_load_on, fail_run_on
        outer = self

        class WhisperModel:
            def __init__(
                self, name: str, device: str, compute_type: str, cpu_threads: int = 0
            ) -> None:
                if device == outer.fail_load_on:
                    raise RuntimeError(f"cannot load on {device}")
                outer.built.append(device)
                self.device = device

            def transcribe(self, audio: Any, **_kw: Any) -> tuple[Any, None]:
                if self.device == outer.fail_run_on:
                    raise RuntimeError(CUBLAS_ERROR)  # raised lazily, on first inference
                return iter([types.SimpleNamespace(text=f" heard on {self.device} ")]), None

        self.module = types.ModuleType("faster_whisper")
        self.module.WhisperModel = WhisperModel  # type: ignore[attr-defined]


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeFasterWhisper) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)


async def test_gpu_failure_at_inference_switches_to_cpu_and_stays_there(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeFasterWhisper(fail_run_on="cuda")
    install(monkeypatch, fake)
    t = FasterWhisperTranscriber("large-v3-turbo", "cuda")
    with caplog.at_level(logging.WARNING):
        assert await t.transcribe(b"x", "audio/webm") == "heard on cpu"
        assert await t.transcribe(b"x", "audio/webm") == "heard on cpu"
    assert fake.built == ["cuda", "cpu"]  # the GPU is not retried on every command
    assert CUBLAS_ERROR in caplog.text and "CPU" in caplog.text  # the log says why and what now


async def test_gpu_failure_at_load_switches_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFasterWhisper(fail_load_on="cuda")
    install(monkeypatch, fake)
    t = FasterWhisperTranscriber("large-v3-turbo", "cuda")
    assert await t.transcribe(b"x", "audio/webm") == "heard on cpu"
    assert fake.built == ["cpu"]


async def test_a_healthy_gpu_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFasterWhisper()
    install(monkeypatch, fake)
    assert (
        await FasterWhisperTranscriber("m", "cuda").transcribe(b"x", "audio/webm")
        == "heard on cuda"
    )
    assert fake.built == ["cuda"]


async def test_a_cpu_failure_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeFasterWhisper(fail_run_on="cpu")
    install(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        await FasterWhisperTranscriber("m", "cpu").transcribe(b"x", "audio/webm")


def test_cuda_dll_dirs_are_found_in_the_nvidia_wheels(tmp_path: Path) -> None:
    for pkg in ("cublas", "cudnn"):
        d = tmp_path / "site" / "nvidia" / pkg / "bin"
        d.mkdir(parents=True)
        (d / f"{pkg}64_12.dll").write_bytes(b"")
    (tmp_path / "site" / "nvidia" / "empty" / "bin").mkdir(parents=True)  # no DLLs: ignored
    found = cuda_dll_dirs([tmp_path / "site", tmp_path / "missing"])
    assert sorted(p.parent.name for p in found) == ["cublas", "cudnn"]


async def test_warm_up_loads_the_model_once_before_the_first_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stt_engine import STTEngine

    fake = FakeFasterWhisper()
    install(monkeypatch, fake)
    t = FasterWhisperTranscriber("m", "cuda")
    engine = STTEngine(t, None)
    await engine.warm_up()
    assert fake.built == ["cuda"]  # loaded already...
    assert await t.transcribe(b"x", "audio/webm") == "heard on cuda"
    assert fake.built == ["cuda"]  # ...and not again


async def test_a_failing_warm_up_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from stt_engine import STTEngine

    fake = FakeFasterWhisper(fail_load_on="cuda")
    install(monkeypatch, fake)
    fake.fail_load_on = "cuda"
    broken = FakeFasterWhisper(fail_load_on="cpu")
    broken.fail_load_on = "cpu"
    install(monkeypatch, broken)
    await STTEngine(FasterWhisperTranscriber("m", "cpu"), None).warm_up()  # logged, not raised
