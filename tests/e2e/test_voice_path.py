"""Real microphone path: Edge MediaRecorder -> PWA chunking -> wire frames -> reassembly -> Whisper.

Edge supplies a synthetic microphone (--use-fake-device-for-media-stream). Needs network the first
time (downloads the tiny.en model) and works on CUDA or CPU.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from playwright.sync_api import Page

from e2e_support import StubServer

pytestmark = pytest.mark.e2e

faster_whisper = pytest.importorskip("faster_whisper")
av = pytest.importorskip("av")


def tap_to_talk(page: Page, talk_ms: int) -> None:
    """Tap the mic on, speak for talk_ms, then tap "Tap to send": the default way to talk."""
    page.get_by_role("button", name="Tap to talk").click()
    page.get_by_role("group", name="Listening").wait_for(timeout=10_000)
    page.wait_for_timeout(talk_ms)
    page.get_by_role("button", name="Send recording").click()


def test_a_real_browser_recording_survives_the_wire_and_decodes(phone, stub: StubServer) -> None:  # type: ignore[no-untyped-def]
    stub.start()
    page: Page = phone(permissions=["microphone"])
    page.add_init_script(
        "localStorage.setItem('cj.pairing', JSON.stringify({roomId:'room-abcdef123', deviceId:'lan-x',"
        " deviceToken:'tok.en.x', expiresAt: 2000000000, deviceName:'Pixel'}))"
    )
    page.goto(stub.url)
    page.wait_for_function(
        "() => { const b = document.querySelector(\"button[aria-label='Tap to talk']\"); return b && !b.disabled; }",
        timeout=15_000,
    )
    tap_to_talk(page, 2500)
    deadline = time.time() + 15
    while time.time() < deadline and not any(m["type"] == "audio.end" for m in stub.messages):
        time.sleep(0.2)
    audio_msgs = [m for m in stub.messages if m["type"].startswith("audio.")]
    assert [m["type"] for m in audio_msgs][0] == "audio.begin" and audio_msgs[-1][
        "type"
    ] == "audio.end"

    begin, end = audio_msgs[0]["payload"], audio_msgs[-1]["payload"]
    chunks = [m for m in audio_msgs if m["type"] == "audio.chunk"]
    assert (
        begin["mime"].startswith("audio/webm") and "opus" in begin["mime"]
    )  # what the daemon will be told
    assert [c["payload"]["seq"] for c in chunks] == list(range(len(chunks)))  # ordered, no gaps
    assert end["total_chunks"] == len(chunks) >= 1
    assert 1500 <= end["duration_ms"] <= 4000
    assert len({m["payload"]["command_id"] for m in audio_msgs}) == 1
    for m in audio_msgs:
        assert len(json.dumps(m).encode()) < 32 * 1024  # fits one API Gateway WebSocket frame
    assert all(len(c["payload"]["b64"]) <= 24 * 1024 for c in chunks)

    # reassemble exactly as the daemon does (strict base64), then decode like faster-whisper does
    raw = b"".join(base64.b64decode(c["payload"]["b64"], validate=True) for c in chunks)
    container = av.open(io.BytesIO(raw))
    stream = container.streams.audio[0]
    assert stream.codec_context.name == "opus"
    container.close()

    from stt_engine import FasterWhisperTranscriber

    async def transcribe() -> str:
        for device in ("cuda", "cpu"):  # same preference order as production
            try:
                return await asyncio.wait_for(
                    FasterWhisperTranscriber("tiny.en", device).transcribe(raw, begin["mime"]), 300
                )
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise last

    # Playwright's sync API owns this thread's event loop, so transcribe in a worker thread.
    # A synthetic beep has no words: success means "decoded and transcribed without error".
    with ThreadPoolExecutor(max_workers=1) as pool:
        text = pool.submit(asyncio.run, transcribe()).result(timeout=400)
    assert isinstance(text, str)
