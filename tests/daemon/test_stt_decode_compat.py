"""Dependency-compatibility guard for voice (found in Phase 3).

faster-whisper decodes browser audio through PyAV. PyAV 19 removed an argument faster-whisper
passes, so on a fresh install EVERY voice command failed with a TypeError - invisible to the
fakes. This test pushes a real WebM/Opus clip (what MediaRecorder produces) through
faster-whisper's own loader; it needs no model download.
"""

from __future__ import annotations

import contextlib
import io
import math
import struct

import pytest

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")
audio = pytest.importorskip("faster_whisper.audio")


def webm_opus(seconds: float = 1.5, hz: int = 440) -> bytes:
    out = io.BytesIO()
    container = av.open(out, "w", format="webm")
    stream = container.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    n = int(48000 * seconds)
    pcm = struct.pack(
        f"<{n}h", *[int(8000 * math.sin(2 * math.pi * hz * i / 48000)) for i in range(n)]
    )
    for off in range(0, n - 960, 960):
        samples = np.frombuffer(pcm[off * 2 : (off + 960) * 2], dtype="<i2").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = 48000
        container.mux(stream.encode(frame))
    container.mux(stream.encode(None))
    container.close()
    return out.getvalue()


def test_browser_style_webm_opus_decodes_to_16khz_mono_float() -> None:
    samples = audio.decode_audio(io.BytesIO(webm_opus(1.5)))
    assert samples.dtype == np.float32
    assert len(samples) == pytest.approx(1.5 * 16000, rel=0.1)  # resampled to Whisper's 16 kHz
    assert float(np.abs(samples).max()) > 0.05  # the tone survived: not silence


def test_av_version_is_in_the_range_requirements_allow() -> None:
    major = int(av.__version__.split(".")[0])
    assert 14 <= major < 19, f"PyAV {av.__version__} is outside requirements.txt (av>=14,<19)"


def test_garbage_audio_fails_fast_with_an_exception_not_a_hang() -> None:
    with pytest.raises(Exception):  # noqa: B017,PT011 - the exact type varies by PyAV version
        audio.decode_audio(io.BytesIO(b"not audio at all"))


def test_a_truncated_recording_never_hangs() -> None:
    data = webm_opus(1.0)
    with contextlib.suppress(Exception):  # a clean error or partial audio are both acceptable
        audio.decode_audio(io.BytesIO(data[: len(data) // 8]))  # a dropped final chunk
