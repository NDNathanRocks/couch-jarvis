"""Subtitles and page search against REAL Edge on pages shaped like the players/sites we meet.

The Heartive-style page reproduces what the live player exposes (found by inspecting it): a
custom control labelled "Subtitle", a menu of buttons ("Upload subtitle", "Off", languages by
their own names), no <track> elements, and the whole player inside an iframe.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from edge_support import free_port
from executors.edge_cdp import EdgeCDP

pytestmark = pytest.mark.edge

HEARTIVE_PLAYER = b"""<!doctype html><title>player</title>
<video id="v" width="640" height="360"></video>
<div style="position:absolute;bottom:10px">
  <button aria-label="Pause" class="group relative cursor-pointer">p</button>
  <button aria-label="Audio" class="group relative cursor-pointer">a</button>
  <button id="cc" aria-label="Subtitle" class="group relative cursor-pointer">cc</button>
  <button aria-label="Settings" class="group relative cursor-pointer">s</button>
</div>
<div id="menu" style="display:none;position:absolute;right:20px;bottom:60px;width:300px;max-height:120px;overflow:auto">
  <button class="group/button inline-flex"><span class="truncate">Upload subtitle</span></button>
  <button class="group/button inline-flex" onclick="pick('off')"><span class="truncate">Off</span></button>
  <button class="group/button inline-flex" onclick="pick('ur')"><span class="truncate">\xd8\xa7\xd9\x8f\xd8\xb1\xd8\xaf\xd9\x8f\xd9\x88</span></button>
  <button class="group/button inline-flex" onclick="pick('es')"><span class="truncate">Espa\xc3\xb1ol</span></button>
  <button class="group/button inline-flex" onclick="pick('fr')"><span class="truncate">Fran\xc3\xa7ais</span></button>
  <button class="group/button inline-flex" onclick="pick('en')"><span class="truncate">English</span></button>
</div>
<script>
window.sub = null;
document.getElementById('cc').onclick = () => {
  const m = document.getElementById('menu'); m.style.display = m.style.display === 'none' ? 'block' : 'none';
};
function pick(code) { window.sub = code; document.getElementById('menu').style.display = 'none'; }
</script>"""

PAGES: dict[str, bytes] = {
    "/heartive-player": HEARTIVE_PLAYER,
    # The menu stays open after a pick (as on the real site) until Esc or a click on the video.
    "/sticky-menu": HEARTIVE_PLAYER.replace(
        b"function pick(code) { window.sub = code; document.getElementById('menu').style.display = 'none'; }",
        b"function pick(code) { window.sub = code; }"
        b"const closeMenu = () => { document.getElementById('menu').style.display = 'none'; };"
        b"document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });"
        b"document.getElementById('v').addEventListener('click', () => { window.videoClicks = (window.videoClicks||0)+1; closeMenu(); });",
    ),
    "/in-iframe": b'<!doctype html><title>show</title><iframe src="/heartive-player" width="700" height="420"></iframe>',
    "/youtube": b"""<!doctype html><title>x - YouTube</title>
<div id="movie_player"><video id="v" width="320" height="180"></video>
<button class="ytp-subtitles-button" aria-pressed="false" style="width:40px;height:30px">cc</button></div>
<script>document.querySelector('.ytp-subtitles-button').onclick = (e) => {
  const b = e.currentTarget; b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); };</script>""",
    "/tracks": b"""<!doctype html><title>tracks</title><video id="v" width="320" height="180"></video>
<script>const v = document.getElementById('v');
v.addTextTrack('subtitles', 'Espa\\u00f1ol', 'es'); v.addTextTrack('subtitles', 'English', 'en');</script>""",
    "/covered-player": HEARTIVE_PLAYER.replace(
        b"</script>",
        b"</script><div id='cover' style='position:fixed;inset:0;z-index:99'></div>"
        b"<script>window.overlayClicked=false;"
        b"document.getElementById('cover').onclick=()=>{window.overlayClicked=true};</script>",
    ),
    "/dead-cc": b"""<!doctype html><title>dead</title><video id="v" width="320" height="180"></video>
<button aria-label="Subtitles">cc</button>""",
    "/no-subtitles": b'<!doctype html><title>plain</title><video id="v" width="320" height="180"></video>',
    "/search-header": b"""<!doctype html><title>shows</title>
<header><a href="#/">Home</a><input placeholder="Search movies, series, people" style="width:300px"></header>
<script>window.searched = null;
document.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter') window.searched = e.target.value; });</script>""",
    "/search-icon": b"""<!doctype html><title>icon</title>
<button aria-label="Search" id="open">&#128269;</button>
<input id="q" type="search" style="display:none">
<script>window.searched = null;
document.getElementById('open').onclick = () => { const q = document.getElementById('q'); q.style.display = 'inline-block'; q.focus(); };
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') window.searched = e.target.value; });</script>""",
    "/no-search": b"<!doctype html><title>none</title><h1>Just text</h1><button>Click</button>",
}


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = PAGES.get(self.path.split("?")[0])
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
async def cdp(edge_port: int) -> Iterator[EdgeCDP]:  # type: ignore[misc]
    client = EdgeCDP(f"http://127.0.0.1:{edge_port}")
    yield client  # type: ignore[misc]
    await client.close()


async def open_page(cdp: EdgeCDP, site: str, path: str) -> object:
    page = await cdp._page()  # noqa: SLF001
    await page.goto(site + path)
    await page.wait_for_load_state("load")
    return page


async def player_state(page: object, expr: str = "window.sub") -> object:
    for frame in page.frames:  # type: ignore[attr-defined]
        try:
            value = await frame.evaluate(expr)
        except Exception:
            continue
        if value is not None:
            return value
    return None


# ------------------------------------------------------------------ subtitles


@pytest.mark.parametrize(("lang", "expected"), [("en", "en"), ("es", "es"), ("fr", "fr")])
async def test_custom_player_menu_picks_the_language(
    cdp: EdgeCDP, site: str, lang: str, expected: str
) -> None:
    page = await open_page(cdp, site, "/heartive-player")
    result = await cdp.set_subtitles("on", lang)
    assert result.ok, result.error
    assert await player_state(page) == expected


async def test_the_language_menu_is_closed_after_picking(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/sticky-menu")
    result = await cdp.set_subtitles("on", "en")
    assert result.ok, result.error
    assert await player_state(page) == "en"
    assert await player_state(page, "document.getElementById('menu').style.display") == "none"


async def test_off_picks_the_off_entry(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/heartive-player")
    assert (await cdp.set_subtitles("on", "en")).ok
    assert (await cdp.set_subtitles("off", "en")).ok
    assert await player_state(page) == "off"


async def test_works_when_the_player_is_inside_an_iframe(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/in-iframe")
    result = await cdp.set_subtitles("on", "en")
    assert result.ok, result.error
    assert await player_state(page) == "en"


async def test_menu_labelled_in_the_languages_own_script_is_understood(
    cdp: EdgeCDP, site: str
) -> None:
    page = await open_page(cdp, site, "/heartive-player")
    assert (await cdp.set_subtitles("on", "es")).ok  # the menu says "Español", not "Spanish"
    assert await player_state(page) == "es"


async def test_youtube_cc_button_is_toggled_to_the_wanted_state(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/youtube")
    button = page.locator(".ytp-subtitles-button")  # type: ignore[attr-defined]
    assert (await cdp.set_subtitles("on", "en")).ok
    assert await button.get_attribute("aria-pressed") == "true"
    assert (await cdp.set_subtitles("on", "en")).ok  # already on: must not toggle it back off
    assert await button.get_attribute("aria-pressed") == "true"
    assert (await cdp.set_subtitles("off", "en")).ok
    assert await button.get_attribute("aria-pressed") == "false"
    assert (await cdp.set_subtitles("toggle", "en")).ok
    assert await button.get_attribute("aria-pressed") == "true"


async def test_native_text_tracks_pick_the_requested_language(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/tracks")
    assert (await cdp.set_subtitles("on", "en")).ok
    modes = await page.evaluate(  # type: ignore[attr-defined]
        "[...document.getElementById('v').textTracks].map(t => t.language + ':' + t.mode)"
    )
    assert modes == ["es:disabled", "en:showing"]
    assert (await cdp.set_subtitles("off", "en")).ok
    assert all(
        m.endswith(":disabled")
        for m in await page.evaluate(  # type: ignore[attr-defined]
            "[...document.getElementById('v').textTracks].map(t => t.language + ':' + t.mode)"
        )
    )


async def test_no_subtitles_control_is_reported_not_faked(cdp: EdgeCDP, site: str) -> None:
    await open_page(cdp, site, "/no-subtitles")
    result = await cdp.set_subtitles("on", "en")
    assert not result.ok and result.fallback_reason == "no_subtitle_control"


async def test_a_covered_control_is_clicked_itself_not_the_overlay_on_top(
    cdp: EdgeCDP, site: str
) -> None:
    """A pause screen or ad layer over the controls must not eat the click (and a click at
    the button's coordinates would land on that layer, e.g. resuming the video)."""
    page = await open_page(cdp, site, "/covered-player")
    result = await cdp.set_subtitles("on", "en")
    assert result.ok, result.error
    assert await player_state(page) == "en"
    assert await page.evaluate("window.overlayClicked") is False  # type: ignore[attr-defined]


async def test_a_control_that_does_nothing_is_not_reported_as_success(
    cdp: EdgeCDP, site: str
) -> None:
    await open_page(cdp, site, "/dead-cc")
    result = await cdp.set_subtitles("on", "en")
    assert not result.ok and result.fallback_reason == "no_subtitle_control"


# ------------------------------------------------------------------ page search


async def test_types_into_the_sites_own_search_bar_and_submits(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/search-header")
    result = await cdp.page_search("big bang theory")
    assert result.ok, result.error
    assert await page.evaluate("window.searched") == "big bang theory"  # type: ignore[attr-defined]


async def test_opens_a_search_icon_to_reach_the_field(cdp: EdgeCDP, site: str) -> None:
    page = await open_page(cdp, site, "/search-icon")
    assert (await cdp.page_search("the office")).ok
    assert await page.evaluate("window.searched") == "the office"  # type: ignore[attr-defined]


async def test_reports_when_there_is_no_search_bar(cdp: EdgeCDP, site: str) -> None:
    await open_page(cdp, site, "/no-search")
    result = await cdp.page_search("anything")
    assert not result.ok and result.fallback_reason == "no_search_bar"
