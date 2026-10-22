"""Adversarial real-browser QA of the phone PWA against a live daemon (Phase 3).

Run:  pytest -m e2e tests/e2e        (needs Microsoft Edge; builds nothing: run `npm run build` first)
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import Page, expect

from e2e_support import PHONES, SHOTS, Daemon, StubServer, pair

pytestmark = pytest.mark.e2e

PhoneFactory = Callable[..., Page]


def badge(page: Page) -> str:
    return page.get_by_role("status", name="Connection", exact=False).first.inner_text()


def wait_badge(page: Page, text: str | re.Pattern[str], timeout: float = 20_000) -> None:
    expect(page.locator("[aria-label^='Connection:']")).to_contain_text(text, timeout=timeout)


# ---------------------------------------------------------------------------- pairing
def test_pairing_removes_the_secret_from_the_url_and_survives_reload(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone()
    pair(page, daemon)
    assert "secret=" not in page.url and "room=" not in page.url
    token = page.evaluate("() => JSON.parse(localStorage.getItem('cj.pairing') || 'null')")
    assert token and token["deviceToken"] and token["deviceId"].startswith("lan-")
    history_len = page.evaluate("() => history.length")
    assert history_len <= 2  # the secret URL was replaced, not pushed
    page.reload()  # no QR this time: the saved token alone must reconnect
    page.get_by_role("button", name="Play / Pause").wait_for()
    wait_badge(page, "Connected")
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_wrong_qr_secret_shows_guidance_and_does_not_spin(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone()
    ident_url = daemon.pair_url.replace("secret=", "secret=WRONG")
    page.goto(ident_url)
    expect(page.get_by_role("alert")).to_contain_text(
        re.compile("scan the QR code on the TV again", re.I), timeout=15_000
    )
    assert page.get_by_role("button", name="Play / Pause").count() == 0
    time.sleep(3)  # no reconnect storm with a bad secret
    assert (
        page.evaluate(
            "() => performance.getEntriesByType('resource').filter(r => r.name.includes('/ws')).length"
        )
        <= 2
    )


# ---------------------------------------------------------------------------- two phones, one TV
def test_two_phones_share_live_state(phone: PhoneFactory, daemon: Daemon) -> None:
    a, b = phone(), phone()
    pair(a, daemon)
    pair(b, daemon)
    a.get_by_label("Type a command").fill("play lofi on youtube")
    a.get_by_role("button", name="Send").click()
    expect(a.get_by_text("Playing lofi")).to_be_visible(timeout=10_000)
    expect(b.get_by_text("Playing lofi").first).to_be_visible(timeout=10_000)  # B sees A's result
    expect(b.locator("[aria-label='Now playing']")).to_contain_text("lofi", timeout=10_000)
    expect(a.get_by_text("Connected · 2")).to_be_visible(timeout=10_000)  # friends bar counts both


def test_button_mashing_never_freezes_the_page_and_reports_rate_limits(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone()
    pair(page, daemon)
    button = page.get_by_role("button", name="Volume up")
    t0 = time.perf_counter()
    for _ in range(40):
        button.click(no_wait_after=True)
    elapsed = time.perf_counter() - t0
    assert elapsed < 15  # the page kept accepting taps
    expect(page.get_by_text("too many taps", exact=False).first).to_be_visible(timeout=10_000)
    # and recovers: after the limiter refills, a press works again
    time.sleep(4)
    button.click()
    expect(page.get_by_text("Volume updated").first).to_be_visible(timeout=10_000)
    assert page.errors == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- drop / reconnect
def test_daemon_restart_reconnects_without_reload_and_controls_gate_correctly(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone()
    pair(page, daemon)
    daemon.stop()
    wait_badge(page, "Reconnecting", timeout=20_000)
    play = page.get_by_role("button", name="Play / Pause")
    expect(play).to_be_disabled()
    expect(page.get_by_role("button", name="Hold to talk")).to_be_disabled()
    # the rest of the UI is alive while offline: typing works, forget-this-phone is reachable
    page.get_by_label("Type a command").fill("still responsive")
    daemon.start()
    wait_badge(page, "Connected", timeout=40_000)
    expect(play).to_be_enabled()
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_text("Done").first).to_be_visible(timeout=10_000)
    assert [e for e in page.errors if "WebSocket" not in e] == []  # type: ignore[attr-defined]


def test_phone_resyncs_after_the_screen_locks_and_unlocks(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone()
    pair(page, daemon)
    # simulate the OS freezing the tab: kill the socket silently, then "wake"
    page.evaluate("() => { for (const k of Object.keys(window)) {} }")
    daemon.stop()
    daemon.start()
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    wait_badge(page, "Connected", timeout=40_000)


# ---------------------------------------------------------------------------- 429 / outage backoff
def test_throttled_handshakes_back_off_without_freezing_the_ui(
    phone: PhoneFactory, stub: StubServer
) -> None:
    stub.throttle_first = 4  # API Gateway answering 429 to the first four upgrades
    stub.start()
    page = phone()
    page.add_init_script(
        "localStorage.setItem('cj.pairing', JSON.stringify({roomId:'room-abcdef123', deviceId:'lan-x',"
        " deviceToken:'tok.en.x', expiresAt: 2000000000, deviceName:'Pixel'}))"
    )
    page.goto(stub.url)
    # responsiveness probe: a 50 ms timer must keep ticking on time through the whole outage
    page.evaluate(
        "() => { window.__gaps = []; let last = performance.now();"
        " setInterval(() => { const n = performance.now(); window.__gaps.push(n - last); last = n; }, 50); }"
    )
    wait_badge(page, "Reconnecting", timeout=10_000)
    expect(page.get_by_role("button", name="Play / Pause")).to_be_disabled()
    page.get_by_label("Type a command").fill("typing during an outage")  # input never blocks
    assert page.get_by_label("Type a command").input_value() == "typing during an outage"
    wait_badge(page, "Connected", timeout=45_000)
    expect(page.get_by_role("button", name="Play / Pause")).to_be_enabled()

    gaps = page.evaluate("() => window.__gaps")
    assert max(gaps) < 600, f"main thread stalled for {max(gaps):.0f} ms"
    stamps = stub.attempts
    deltas = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert len(stamps) >= 5  # 4 throttled + the one that finally connected
    assert all(d >= 0.45 for d in deltas), (
        f"hot reconnect loop: {deltas}"
    )  # never faster than the floor
    assert (
        deltas[3] > deltas[0] or max(deltas) > 2.0
    )  # and it backs off rather than polling at a fixed rate
    assert page.errors == []  # type: ignore[attr-defined]


def test_sustained_outage_does_not_hammer_the_server(phone: PhoneFactory, stub: StubServer) -> None:
    stub.throttle_first = 10_000  # relay down for the whole test
    stub.start()
    page = phone()
    page.add_init_script(
        "localStorage.setItem('cj.pairing', JSON.stringify({roomId:'room-abcdef123', deviceId:'lan-x',"
        " deviceToken:'tok.en.x', expiresAt: 2000000000, deviceName:'Pixel'}))"
    )
    page.goto(stub.url)
    time.sleep(25)
    assert len(stub.attempts) <= 12, f"{len(stub.attempts)} connection attempts in 25 s"
    assert len(stub.attempts) >= 3  # but it keeps trying
    wait_badge(
        page, re.compile("Reconnecting|Can't connect"), timeout=5_000
    )  # Reconnecting… or Re-pair needed: never a blank/frozen badge
    page.get_by_label("Type a command").fill("x")  # still interactive


def test_server_side_rate_limit_rejections_are_explained_and_recoverable(
    phone: PhoneFactory, stub: StubServer
) -> None:
    stub.reject_commands = True
    stub.start()
    page = phone()
    page.add_init_script(
        "localStorage.setItem('cj.pairing', JSON.stringify({roomId:'room-abcdef123', deviceId:'lan-x',"
        " deviceToken:'tok.en.x', expiresAt: 2000000000, deviceName:'Pixel'}))"
    )
    page.goto(stub.url)
    wait_badge(page, "Connected")
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_text("too many taps", exact=False)).to_be_visible(timeout=5_000)
    stub.reject_commands = False
    page.get_by_role("button", name="Next").click()  # UI is not stuck in a "busy" state
    assert any(m["type"] == "cmd.button" for m in stub.messages)


def test_a_command_the_pc_never_answers_times_out(phone: PhoneFactory, stub: StubServer) -> None:
    stub.start()  # the stub accepts commands but never replies with a result
    page = phone()
    page.add_init_script(
        "localStorage.setItem('cj.pairing', JSON.stringify({roomId:'room-abcdef123', deviceId:'lan-x',"
        " deviceToken:'tok.en.x', expiresAt: 2000000000, deviceName:'Pixel'}))"
    )
    page.clock.install()
    page.goto(stub.url)
    wait_badge(page, "Connected")
    page.get_by_label("Type a command").fill("play something")
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_text("Sending...")).to_be_visible()
    # The expiry check polls every 5 s and the toast auto-dismisses after 4 s, so the exact tick
    # depends on when the command was sent. Step the fake clock a second at a time past the 45 s
    # limit and stop as soon as the toast shows, instead of assuming where the tick lands.
    toast = page.get_by_text("No answer from the PC")
    for _ in range(70):
        page.clock.run_for(1_000)
        if toast.count():
            break
    expect(toast).to_be_visible(timeout=5_000)
    expect(page.get_by_text("Sending...")).to_have_count(0)


# ---------------------------------------------------------------------------- hostile web pages (real browser)
def test_another_website_cannot_drive_the_daemon_over_websocket(
    phone: PhoneFactory, daemon: Daemon, evil_site: str
) -> None:
    page = phone()
    page.goto(evil_site)
    result = page.evaluate(
        """(port) => new Promise((resolve) => {
          const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
          let opened = false;
          ws.onopen = () => { opened = true; };
          ws.onmessage = () => resolve('received-data');
          ws.onclose = (e) => resolve(opened ? 'opened-then-closed' : 'refused');
          ws.onerror = () => {};
          setTimeout(() => resolve('timeout'), 4000);
        })""",
        daemon.port,
    )
    assert result == "refused", result
    assert httpx.get(f"{daemon.url}/healthz").json()["queue_depth"] == 0


def test_another_website_cannot_post_commands_or_read_state(
    phone: PhoneFactory, daemon: Daemon, evil_site: str
) -> None:
    page = phone()
    page.goto(evil_site)
    out = page.evaluate(
        """async (base) => {
          const r = {};
          try { await fetch(base + '/api/command', {method: 'POST', mode: 'no-cors', headers: {'Content-Type': 'text/plain'},
                body: JSON.stringify({type: 'cmd.button', action: 'panic', idempotency_key: 'csrf-' + Date.now()})}); r.post = 'sent'; }
          catch (e) { r.post = 'blocked'; }
          try { const m = await fetch(base + '/api/metrics'); r.read = 'readable:' + m.status; }
          catch (e) { r.read = 'blocked'; }
          try { const es = new EventSource(base + '/events'); r.sse = await new Promise(res => { es.onmessage = () => res('streaming'); es.onerror = () => res('blocked'); setTimeout(() => res('timeout'), 3000); es.addEventListener('state.snapshot', () => res('streaming')); }); es.close(); }
          catch (e) { r.sse = 'blocked'; }
          return r;
        }""",
        daemon.url,
    )
    assert out["read"] == "blocked", out  # CORS: the page can never read responses
    assert out["sse"] in ("blocked", "timeout"), out
    time.sleep(0.5)
    metrics = httpx.get(f"{daemon.url}/api/metrics").json()
    assert metrics["commands"] == 0, f"a cross-site POST executed a command: {out}"


# ---------------------------------------------------------------------------- layout, touch targets, shifts
@pytest.mark.parametrize("name", list(PHONES))
def test_layout_touch_targets_overflow_and_layout_shift(
    phone: PhoneFactory, daemon: Daemon, name: str
) -> None:
    viewport = PHONES[name]
    page = phone(viewport)
    page.add_init_script(
        "window.__cls = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })"
        ".observe({type: 'layout-shift', buffered: true});"
    )
    pair(page, daemon)
    page.get_by_label("Type a command").fill("play lofi on youtube")
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_text("Playing lofi").first).to_be_visible(timeout=10_000)
    page.wait_for_timeout(800)

    report = page.evaluate(
        """() => {
          const vw = document.documentElement.clientWidth;
          const small = [], offscreen = [];
          for (const el of document.querySelectorAll('button, input, a, [role=button]')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (r.width < 44 || r.height < 44) small.push(`${el.getAttribute('aria-label') || el.textContent.trim()}: ${Math.round(r.width)}x${Math.round(r.height)}`);
            if (r.left < -1 || r.right > vw + 1) offscreen.push(el.getAttribute('aria-label') || el.textContent.trim());
          }
          return {
            overflowX: document.documentElement.scrollWidth - vw,
            small, offscreen, cls: window.__cls, vw,
            fontMin: Math.min(...[...document.querySelectorAll('p, span, h1, h2, button')].filter(e => e.textContent.trim()).map(e => parseFloat(getComputedStyle(e).fontSize))),
          };
        }"""
    )
    SHOTS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in name)
    page.screenshot(path=str(SHOTS / f"phone_{safe}.png"), full_page=True)
    assert report["overflowX"] <= 0, f"horizontal scroll of {report['overflowX']}px at {name}"
    assert report["offscreen"] == [], f"controls outside the viewport: {report['offscreen']}"
    assert report["small"] == [], f"touch targets under 44x44 CSS px: {report['small']}"
    assert report["cls"] < 0.1, f"layout shift score {report['cls']:.3f} (good < 0.1)"
    assert report["fontMin"] >= 10.5, f"text too small to read: {report['fontMin']}px"
    assert page.errors == []  # type: ignore[attr-defined]


def test_text_zoom_and_long_content_do_not_break_the_layout(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    page = phone({"width": 360, "height": 740})
    pair(page, daemon)
    page.add_style_tag(content="html { font-size: 22px !important; }")  # accessibility text scaling
    page.get_by_label("Type a command").fill("play " + "a very long query " * 20)
    page.wait_for_timeout(300)
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    page.screenshot(path=str(SHOTS / "phone_zoomed.png"), full_page=True)


def test_hold_to_talk_degrades_gracefully_without_a_secure_context(
    phone: PhoneFactory, daemon: Daemon
) -> None:
    """On the LAN (plain http://192.168.x.x) browsers hide the microphone API entirely."""
    page = phone()
    page.add_init_script("Object.defineProperty(navigator, 'mediaDevices', {value: undefined})")
    pair(page, daemon)
    assert page.get_by_role("button", name="Hold to talk").is_disabled()
    expect(page.get_by_text("secure (https)")).to_be_visible()
    page.get_by_label("Type a command").fill("pause")  # the text path still works
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_text("Done").first).to_be_visible(timeout=10_000)
