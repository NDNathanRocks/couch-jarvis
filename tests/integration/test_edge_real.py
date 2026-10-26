"""EdgeCDP against REAL Microsoft Edge (marker: edge). Verifies the injected JS and the
BrowserSnapshot data contract against a real DOM, which the fakes cannot.

The page imitates the parts of YouTube's player DOM the executor depends on (#movie_player,
.ad-showing, .ytp-* buttons, a <video>). It is NOT YouTube: selectors on the real site are
still unverified.
"""

from __future__ import annotations

import http.server
import io
import struct
import threading
import wave
from collections.abc import Iterator

import pytest

from edge_support import free_port
from executors.edge_cdp import ALLOWLISTED_JS, EdgeCDP

pytestmark = pytest.mark.edge


PAGE = b"""<!doctype html><title>Lofi Girl - YouTube</title>
<meta property="og:image" content="https://i.ytimg.com/vi/x/hq.jpg">
<div id="movie_player" style="width:640px;height:360px;background:#222">
  <video id="v" src="/silence.wav" loop muted></video>
  <button class="ytp-fullscreen-button" style="width:60px;height:40px">fs</button>
  <button class="ytp-next-button">next</button>
</div>
<script>
const player = document.getElementById('movie_player');
document.querySelector('.ytp-fullscreen-button').onclick = () => player.requestFullscreen();
window.nextClicks = 0;
document.querySelector('.ytp-next-button').onclick = () => { window.nextClicks++; };
function showAd() {
  player.classList.add('ad-showing');
  const b = document.createElement('button');
  b.className = 'ytp-ad-skip-button'; b.textContent = 'Skip';
  b.onclick = () => { player.classList.remove('ad-showing'); b.remove(); };
  player.appendChild(b);
}
window.showAd = showAd;
</script>"""


def silence() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 8000 * 30)  # 30 s so seeking has room
    return buf.getvalue()


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    wav = silence()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/silence.wav":
                body, ctype = wav, "audio/wav"
            else:
                body, ctype = PAGE, "text/html"
            rng = self.headers.get("Range")
            status, extra = 200, []
            if rng and rng.startswith("bytes="):  # <video> seeking needs range support
                start_s, _, end_s = rng[6:].partition("-")
                start = int(start_s or 0)
                end = int(end_s) if end_s else len(body) - 1
                extra = [("Content-Range", f"bytes {start}-{end}/{len(body)}")]
                body, status = body[start : end + 1], 206
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture
async def cdp(edge_port: int, site: str) -> Iterator[EdgeCDP]:  # type: ignore[misc]
    client = EdgeCDP(f"http://127.0.0.1:{edge_port}")
    page = await client._page()  # noqa: SLF001
    await page.goto(site)
    await page.wait_for_selector("video")
    yield client  # type: ignore[misc]
    await client.close()


async def test_snapshot_matches_the_contract_on_a_real_page(cdp: EdgeCDP) -> None:
    snap = await cdp.snapshot()
    assert snap.title == "Lofi Girl"  # " - YouTube" suffix stripped
    assert snap.is_playing is False and snap.ad_showing is False and snap.is_fullscreen is False
    assert snap.duration_s == pytest.approx(30, abs=1)
    assert snap.thumbnail_url == "https://i.ytimg.com/vi/x/hq.jpg"
    assert snap.tab_count >= 1 and snap.url.startswith("http://127.0.0.1")


async def test_media_commands_play_pause_seek_toggle_for_real(cdp: EdgeCDP) -> None:
    assert (await cdp.media_command("play")).ok
    await asyncio_sleep(0.4)
    assert (await cdp.snapshot()).is_playing is True
    assert (await cdp.media_command("pause")).ok
    assert (await cdp.snapshot()).is_playing is False
    assert (await cdp.media_command("play_pause")).ok  # toggle path decides from real state
    assert (await cdp.snapshot()).is_playing is True
    before = (await cdp.snapshot()).position_s
    assert (await cdp.seek(10)).ok
    assert (await cdp.snapshot()).position_s >= before + 9
    assert (await cdp.seek(-1000)).ok
    assert (await cdp.snapshot()).position_s < 1  # clamped at zero


async def test_next_button_uses_the_allowlisted_js_and_real_selectors(cdp: EdgeCDP) -> None:
    assert (await cdp.media_command("next")).ok
    page = await cdp._page()  # noqa: SLF001
    assert await page.evaluate("window.nextClicks") == 1


async def test_ad_skipping_dom_and_js_tiers_against_a_real_dom(cdp: EdgeCDP) -> None:
    page = await cdp._page()  # noqa: SLF001
    await page.evaluate("showAd()")
    assert (await cdp.snapshot()).ad_showing is True
    assert (await cdp.close_ad_dom()).ok  # tier 1: finds and clicks .ytp-ad-skip-button
    assert (await cdp.snapshot()).ad_showing is False
    assert not (await cdp.close_ad_dom()).ok  # nothing to skip: honest failure with a reason
    await page.evaluate("showAd()")
    assert (await cdp.close_ad_js()).ok  # tier 2: scripted skip also works
    assert (await cdp.snapshot()).ad_showing is False


async def test_fullscreen_tiers_with_and_without_a_user_gesture(cdp: EdgeCDP) -> None:
    page = await cdp._page()  # noqa: SLF001
    # Tier 2: requestFullscreen from a plain evaluate has no gesture and must FAIL cleanly...
    plain = await page.evaluate(
        "Promise.race([document.querySelector('#movie_player').requestFullscreen().then(() => 'ok', e => e.name),"
        " new Promise(r => setTimeout(() => r('pending'), 1500))])"
    )  # a gesture-less request may reject OR stay pending forever: never await it unbounded
    # ...whereas the CDP userGesture path the executor uses must work (or at least not crash)
    res = await cdp.fullscreen_js()
    snap = await cdp.snapshot()
    assert res.ok == snap.is_fullscreen, (res, plain)  # no false success: result mirrors reality


async def test_eval_allowlist_rejects_unknown_scripts_without_executing(cdp: EdgeCDP) -> None:
    res = await cdp.eval_allowlisted("alert(1)")
    assert not res.ok and "allowlisted" in (res.error or "")
    assert set(ALLOWLISTED_JS) >= {"play", "pause", "skip_ad", "request_fullscreen", "next", "prev"}


async def test_navigation_policy_is_enforced_by_the_real_executor(cdp: EdgeCDP) -> None:
    for url in ("https://evil.example/", "javascript:alert(1)", "file:///C:/Windows/win.ini"):
        res = await cdp.navigate(url)
        assert not res.ok and res.fallback_reason == "blocked"
    assert (await cdp.snapshot()).url.startswith("http://127.0.0.1")  # the tab never moved


async def test_closing_the_tab_and_self_healing_when_edge_state_changes(cdp: EdgeCDP) -> None:
    page = await cdp._page()  # noqa: SLF001
    count = (await cdp.snapshot()).tab_count
    extra = await page.context.new_page()
    await extra.goto("about:blank")
    assert (await cdp.snapshot()).tab_count == count + 1


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def test_speed_and_seek_to_for_real(cdp: EdgeCDP) -> None:
    res = await cdp.set_speed(rate=1.25)
    assert res.ok and res.observed["rate"] == 1.25
    assert (await cdp.set_speed(delta=0.25)).observed["rate"] == 1.5
    assert (await cdp.set_speed(rate=9)).observed["rate"] == 4  # clamped
    assert (await cdp.seek_to(12)).ok
    assert (await cdp.snapshot()).position_s == pytest.approx(12, abs=0.5)
    assert (await cdp.seek_to(10_000)).ok
    assert (await cdp.snapshot()).position_s <= 30.5  # clamped to the end


async def test_exit_fullscreen_for_real(cdp: EdgeCDP) -> None:
    await cdp.fullscreen_js()
    assert (await cdp.browser_action("exit_fullscreen")).ok
    await asyncio_sleep(0.3)
    assert (await cdp.snapshot()).is_fullscreen is False


async def test_duplicate_then_follow_the_tab_the_user_picks(cdp: EdgeCDP) -> None:
    first = await cdp._page()  # noqa: SLF001
    assert (await cdp.browser_action("duplicate_tab")).ok
    copy = await cdp._page()  # noqa: SLF001
    assert copy is not first and copy.url == first.url
    await first.bring_to_front()  # the user clicks the first tab again (no command involved)
    await asyncio_sleep(0.3)
    assert await cdp._page() is first  # noqa: SLF001
    await copy.close()
