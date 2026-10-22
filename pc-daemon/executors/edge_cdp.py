"""Self-healing Playwright-over-CDP manager for Microsoft Edge, YouTube and YouTube Music.

Only a fixed allowlist of JS snippets is ever evaluated; LLM output can name one but never
supply code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urlparse

from agent.policy import url_allowed
from executors.base import ActionResult, BrowserSnapshot, MediaCommand, Tier
from executors.page_items import ITEM_NOT_FOUND, REDIRECT_BLOCKED, Candidate, pick_item

log = logging.getLogger(__name__)

SKIP_AD_SELECTORS = ", ".join(
    [
        ".ytp-skip-ad-button",
        ".ytp-ad-skip-button",
        ".ytp-ad-skip-button-modern",
        ".ytp-ad-overlay-close-button",
        "button[aria-label^='Skip']",
    ]
)

JS_SNAPSHOT = """() => {
  const v = document.querySelector('video');
  const player = document.querySelector('#movie_player');
  const host = location.hostname;
  const musicTitle = document.querySelector('ytmusic-player-bar .title');
  const og = document.querySelector('meta[property="og:image"]');
  return {
    url: location.href,
    title: (musicTitle ? musicTitle.textContent : document.title).replace(/ - YouTube( Music)?$/, '').trim(),
    host: host,
    is_playing: v ? (!v.paused && !v.ended) : null,
    is_fullscreen: !!document.fullscreenElement,
    ad_showing: !!(player && player.classList.contains('ad-showing')),
    position_s: v ? v.currentTime : 0,
    duration_s: (v && isFinite(v.duration)) ? v.duration : 0,
    thumbnail_url: og ? og.content : null,
  };
}"""

# name -> JS source. The router may only pick a name.
ALLOWLISTED_JS: dict[str, str] = {
    "play": "() => { const v = document.querySelector('video'); if (v) { v.play(); return true; } return false; }",
    "pause": "() => { const v = document.querySelector('video'); if (v) { v.pause(); return true; } return false; }",
    "skip_ad": """() => {
      const player = document.querySelector('#movie_player');
      const v = document.querySelector('video');
      if (!(player && player.classList.contains('ad-showing')) || !v) return false;
      if (isFinite(v.duration) && v.duration > 0) v.currentTime = v.duration;
      document.querySelectorAll('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button').forEach(b => b.click());
      document.querySelectorAll('.ytp-ad-overlay-slot, ytd-ad-slot-renderer').forEach(n => n.remove());
      return true;
    }""",
    "request_fullscreen": """() => {
      const area = (e) => { const r = e.getBoundingClientRect(); return r.width * r.height; };
      const biggest = (list) => list.sort((a, b) => area(b) - area(a))[0];
      let el = document.querySelector('#movie_player');
      if (!el) {
        const video = biggest([...document.querySelectorAll('video')]);
        if (video) {
          // Fullscreen the player wrapper, not the bare <video>, so the site's own controls stay.
          el = video;
          const va = area(video);
          while (el.parentElement && el.parentElement !== document.body
                 && area(el.parentElement) <= va * 1.25) el = el.parentElement;
        } else {
          // The player lives in a child frame: fullscreen the frame that holds it.
          el = biggest([...document.querySelectorAll('iframe')].filter(f => area(f) > 300 * 150));
        }
      }
      if (!el || !el.requestFullscreen) return false;
      el.requestFullscreen();
      return true;
    }""",
    "next": """() => {
      const b = document.querySelector('.ytp-next-button, ytmusic-player-bar .next-button');
      if (!b) return false; b.click(); return true;
    }""",
    "prev": """() => {
      const b = document.querySelector('.ytp-prev-button, ytmusic-player-bar .previous-button');
      if (!b) return false; b.click(); return true;
    }""",
}


FULLSCREEN_SELECTORS = ", ".join(
    [
        ".ytp-fullscreen-button",
        "button[aria-label*='ullscreen' i]",
        "button[aria-label*='full screen' i]",
        "[title*='ullscreen' i]",
        "[title*='full screen' i]",
        ".vjs-fullscreen-control",
        ".plyr__control[data-plyr='fullscreen']",
        ".jw-icon-fullscreen",
        "button[class*='fullscreen' i]",
        "[role='button'][class*='fullscreen' i]",
    ]
)

# Finds the main thing worth hovering/fullscreening: the biggest <video>, else the biggest iframe.
JS_PLAYER_BOX = """() => {
  const boxes = [...document.querySelectorAll('video, iframe')].map(e => {
    const r = e.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height};
  }).filter(b => b.w > 200 && b.h > 100 && b.y < innerHeight && b.y + b.h > 0);
  boxes.sort((a, b) => b.w * b.h - a.w * a.h);
  return boxes[0] || {x: 0, y: 0, w: innerWidth, h: innerHeight};
}"""

# One entry per outermost clickable thing, in DOM order, remembered in window.__cjItems so the
# caller can click by index. Inputs and submit buttons are never offered.
JS_COLLECT_ITEMS = """(cap) => {
  const resultSel = 'ytd-video-renderer a#video-title, ytd-rich-item-renderer a#video-title-link,'
    + ' ytmusic-responsive-list-item-renderer a[href^="watch"], ytd-playlist-renderer a#video-title';
  const results = new Set(document.querySelectorAll(resultSel));
  const skip = /^(INPUT|TEXTAREA|SELECT|SCRIPT|STYLE|NOSCRIPT|VIDEO|IFRAME|BODY|HTML)$/;
  const semantic = 'a[href],button:not([type=submit]),[role=button],[role=link],[role=tab],[onclick]';
  const label = /(^|[-_ ])(title|head|header|row|item|card|ep|episode|name|result|entry)([-_ ]|$)/i;
  const items = [], out = [];
  let last = null, scanned = 0;
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (++scanned > 8000 || items.length >= cap) break;
    if (last && last.contains(el)) continue;
    if (skip.test(el.tagName)) continue;
    let ok = results.has(el) || el.matches(semantic);
    if (!ok) {
      if (el.children.length > 6) continue;
      const t = (el.innerText || '').trim();
      if (!t || t.length > 160) continue;
      ok = getComputedStyle(el).cursor === 'pointer';
      if (!ok && t.length <= 80) {
        // Many sites handle clicks on a parent (event delegation), so rows carry no onclick or
        // pointer cursor. A person still clicks the row's title, so offer headings and row-like
        // blocks too and click the heading inside.
        ok = /^H[1-6]$/.test(el.tagName) || el.tagName === 'LI' || label.test(el.className);
      }
    }
    if (!ok) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const text = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
    if (!text || text.length > 200) continue;
    last = el;
    out.push({index: items.length, text: text.slice(0, 200), group: results.has(el) ? 'result' : '',
              onscreen: r.bottom > 0 && r.top < innerHeight});
    items.push(el.querySelector('h1,h2,h3,h4,h5,h6,a[href]') || el);
  }
  window.__cjItems = items;
  return out;
}"""

# Picking a row's *title* often does nothing (it may just expand a description): the thing that
# plays is a thumbnail/play button or a link in the same row. Titles that are already links or
# buttons are left alone.
JS_PLAY_TARGET = """(el) => {
  if (el.matches('a[href],button,[role=button],[role=link]')) return el;
  const rowTok = /(^|\\s)(ep|episode|item|card|row|entry|result|tile)(\\s|$)/i;
  let row = el.parentElement;
  while (row && row !== document.body && !(row.tagName === 'LI' || rowTok.test(row.className))) {
    row = row.parentElement;
  }
  if (!row || row === document.body) return el;
  const bad = /mark|watched|torrent|expand|descript|check|favou?rite|list|more|menu|download|share|trailer/i;
  const good = /play|still|thumb|poster|cover|watch/i;
  const controls = [...row.querySelectorAll('a[href],button,[role=button]')].filter(c => {
    const r = c.getBoundingClientRect();
    return r.width > 4 && r.height > 4
      && !bad.test(`${c.className} ${c.getAttribute('aria-label') || ''} ${c.title || ''} ${c.getAttribute('href') || ''}`);
  });
  const tag = (c) => `${c.className} ${c.getAttribute('aria-label') || ''} ${c.title || ''}`;
  return controls.find(c => good.test(tag(c))) || controls[0] || el;
}"""

JS_VIDEO_STATE = """() => {
  const v = document.querySelector('video');
  return v ? {is_playing: !v.paused && !v.ended, position_s: v.currentTime,
              duration_s: isFinite(v.duration) ? v.duration : 0} : null;
}"""


def classify_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "intercepts pointer events" in text or "overlay" in text:
        return "overlay"
    if "cross-origin" in text or ("frame" in text and "detached" in text):
        return "iframe_cross_origin"
    if "timeout" in text:
        return "selector_timeout"
    if "gesture" in text or "fullscreen" in text:
        return "no_user_gesture"
    return "dom_error"


async def _close_quietly(page: Any) -> None:
    with contextlib.suppress(Exception):  # already gone
        await page.close()


def blocks_navigation(start_url: str, target_url: str) -> bool:
    """True when loading target_url would move the tab to a different website."""
    target = urlparse(target_url)
    if target.scheme not in ("http", "https"):
        return False  # about:blank, data: and blob: frames are not a site change
    return (target.hostname or "").lower() != (urlparse(start_url).hostname or "").lower()


async def _safe_count(locator: Any, *, timeout_s: float = 2.0) -> int:
    """locator.count() that treats a frame with no script context (still loading) as empty."""
    try:
        return int(await asyncio.wait_for(locator.count(), timeout_s))
    except Exception:
        return 0


async def _first_in_frames(
    page: Any, js: str, arg: Any = None, *, frame_timeout_s: float = 2.0
) -> Any:
    """Run js in the main frame, then each child frame, until one returns something truthy.

    A freshly created player iframe can sit without a script context for a long time and
    Playwright's evaluate waits for it forever, so every frame gets its own short deadline.
    """
    for frame in page.frames:
        try:
            call = frame.evaluate(js, arg) if arg is not None else frame.evaluate(js)
            value = await asyncio.wait_for(call, frame_timeout_s)
        except Exception:  # timed out, navigated away or detached: try the next frame
            continue
        if value:
            return value
    return False


def _source_for(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host.startswith("music.youtube"):
        return "ytmusic"
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    return "other"


class EdgeCDP:
    def __init__(
        self,
        cdp_url: str,
        *,
        ensure_edge: Callable[[], Awaitable[bool]] | None = None,
        connect_attempts: int = 3,
    ) -> None:
        self._cdp_url = cdp_url
        self._ensure_edge = ensure_edge
        self._attempts = connect_attempts
        self._pw: Any = None
        self._browser: Any = None
        self._page_ref: Any = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ connection

    async def _connect(self) -> None:
        from playwright.async_api import async_playwright

        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return
            if self._pw is None:
                self._pw = await async_playwright().start()
            last: BaseException | None = None
            for attempt in range(self._attempts):
                try:
                    self._browser = await self._pw.chromium.connect_over_cdp(self._cdp_url)
                    return
                except Exception as exc:
                    last = exc
                    if self._ensure_edge is not None:
                        await self._ensure_edge()  # watchdog relaunches Edge if it is gone
                    await asyncio.sleep(0.5 * 2**attempt)
            raise ConnectionError(f"cannot reach Edge CDP at {self._cdp_url}") from last

    async def is_alive(self) -> bool:
        return self._browser is not None and bool(self._browser.is_connected())

    async def close(self) -> None:
        if self._pw is not None:
            await self._pw.stop()
            self._pw = self._browser = self._page_ref = None

    async def _page(self) -> Any:
        await self._connect()
        if self._page_ref is not None and not self._page_ref.is_closed():
            return self._page_ref  # sticky: an ad popup must never become the page we control
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        pages = [p for p in context.pages if not p.is_closed()]
        media = [p for p in pages if _source_for(p.url) != "other"]
        web = [p for p in pages if p.url.startswith(("http://", "https://"))]
        if media:
            page = media[-1]
        elif web:
            page = web[0]  # the oldest tab is the one the TV was already showing
        else:
            page = pages[-1] if pages else await context.new_page()
        self._adopt(page)  # ad scripts open popups when the player is clicked: close them
        return page

    @contextlib.asynccontextmanager
    async def _hold_origin(self, page: Any) -> AsyncIterator[list[str]]:
        """While an in-page action runs, undo any redirect of the tab to a different site.

        Yields the list of URLs the site tried to send us to so the caller can tell the user.
        Request interception is deliberately not used: it stalls cross-origin player iframes.
        """
        start, hijacks = page.url, []

        def on_nav(frame: Any) -> None:
            if frame == page.main_frame and blocks_navigation(start, frame.url):
                hijacks.append(frame.url)

        page.on("framenavigated", on_nav)
        try:
            yield hijacks
        finally:
            page.remove_listener("framenavigated", on_nav)
            if hijacks or blocks_navigation(start, page.url):
                hijacks.append(page.url)
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=8000)
                except Exception:
                    await page.goto(start, wait_until="domcontentloaded", timeout=8000)

    async def _guard(
        self, fn: Callable[[Any], Awaitable[ActionResult]], tier: Tier = "dom"
    ) -> ActionResult:
        try:
            page = await self._page()
            return await fn(page)
        except Exception as exc:
            return ActionResult(
                False, tier, f"{type(exc).__name__}: {str(exc)[:160]}", classify_error(exc)
            )

    # ------------------------------------------------------------------ actions

    async def navigate(self, url: str) -> ActionResult:
        if not url_allowed(url):  # defense in depth: the agent already checks this
            return ActionResult(False, "dom", "url is not on the media allowlist", "blocked")

        async def go(page: Any) -> ActionResult:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return ActionResult(True, "dom")

        return await self._guard(go)

    async def search_and_play(self, query: str, source: str, *, play: bool = True) -> ActionResult:
        """Search; with play=False stop on the results page so the user can choose what to open."""

        async def go(page: Any) -> ActionResult:
            if source == "ytmusic":
                await page.goto(
                    f"https://music.youtube.com/search?q={quote_plus(query)}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                first = page.locator("ytmusic-responsive-list-item-renderer a[href^='watch']").first
            else:
                await page.goto(
                    f"https://www.youtube.com/results?search_query={quote_plus(query)}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                first = page.locator("ytd-video-renderer a#video-title").first
            await first.wait_for(timeout=8000)
            if not play:
                return ActionResult(True, "dom")
            await first.click(timeout=5000)
            await page.wait_for_selector("video", timeout=10000)
            await page.evaluate(ALLOWLISTED_JS["play"])
            return ActionResult(True, "dom")

        return await self._guard(go)

    async def list_items(self, limit: int = 40) -> list[Candidate]:
        """Clickable things on the page, on-screen ones first, for the router and for picking."""
        try:
            page = await self._page()
            raw = await page.evaluate(JS_COLLECT_ITEMS, 200)
        except Exception:
            return []
        shown = [r for r in raw if r["onscreen"]] or raw
        return [Candidate(r["index"], r["text"], r["group"]) for r in shown[:limit]]

    async def click_item(
        self,
        *,
        index: int | None = None,
        text: str | None = None,
        episode: int | None = None,
        result: int | None = None,
    ) -> ActionResult:
        async def click(page: Any) -> ActionResult:
            raw = await page.evaluate(JS_COLLECT_ITEMS, 200)
            items = [Candidate(r["index"], r["text"], r["group"]) for r in raw]
            picked = pick_item(items, index=index, text=text, episode=episode, result=result)
            if picked is None:
                return ActionResult(False, "dom", ITEM_NOT_FOUND, "item_not_found")
            target = await page.evaluate_handle(
                f"(i) => ({JS_PLAY_TARGET})(window.__cjItems[i])", picked
            )
            handle = target.as_element()
            if handle is None:
                return ActionResult(False, "dom", ITEM_NOT_FOUND, "item_not_found")
            await handle.scroll_into_view_if_needed(timeout=3000)
            async with self._hold_origin(page) as blocked:
                try:
                    await handle.click(timeout=4000)
                except Exception:
                    await handle.evaluate("(e) => e.click()")  # covered by an overlay
                await asyncio.sleep(1.0)  # let a single-page app swap views before we report back
            if blocked:
                return ActionResult(False, "dom", REDIRECT_BLOCKED, "off_site")
            return ActionResult(True, "dom")

        return await self._guard(click)

    async def wake_controls(self) -> None:
        """Move the pointer over the player: most players hide their control bar until it moves."""
        try:
            page = await self._page()
            await page.bring_to_front()
            box = await page.evaluate(JS_PLAYER_BOX)
            cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
            for dx, dy in ((-40, -15), (30, 20), (0, 0)):
                await page.mouse.move(cx + dx, cy + dy, steps=5)
            await asyncio.sleep(0.2)
        except Exception:
            log.warning("wake_controls failed", exc_info=True)

    async def click_selector(self, selector: str, timeout_ms: int) -> ActionResult:
        async def click(page: Any) -> ActionResult:
            await page.locator(selector).first.click(timeout=timeout_ms)
            return ActionResult(True, "dom")

        return await self._guard(click)

    async def media_command(self, cmd: MediaCommand) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            name: str = cmd
            if cmd == "play_pause":
                snap = await self.snapshot()
                name = "pause" if snap.is_playing else "play"
            ok = await _first_in_frames(page, ALLOWLISTED_JS[name])
            return ActionResult(
                bool(ok),
                "dom",
                None if ok else "no media element",
                None if ok else "selector_timeout",
            )

        return await self._guard(run)

    async def seek(self, delta_s: int) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            ok = await _first_in_frames(
                page,
                "(d) => { const v = document.querySelector('video'); if (!v) return false;"
                " v.currentTime = Math.max(0, v.currentTime + d); return true; }",
                delta_s,
            )
            return ActionResult(bool(ok), "dom", None if ok else "no media element")

        return await self._guard(run)

    async def close_tab(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            await page.close()
            self._page_ref = None
            return ActionResult(True, "dom")

        return await self._guard(run)

    async def browser_action(self, action: str) -> ActionResult:
        """Back / forward / reload, tab switching, and scrolling for the current tab."""

        async def run(page: Any) -> ActionResult:
            if action in ("back", "forward"):
                async with self._hold_origin(page) as hijacked:
                    go = page.go_back if action == "back" else page.go_forward
                    moved = await go(wait_until="domcontentloaded", timeout=8000)
                if moved is None and not hijacked:
                    return ActionResult(
                        False, "dom", f"There is no page to go {action} to.", "no_history"
                    )
                return ActionResult(True, "dom")
            if action == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=15000)
                return ActionResult(True, "dom")
            if action in ("scroll_up", "scroll_down"):
                box = await page.evaluate(JS_PLAYER_BOX)  # a viewport-sized box when no player
                await page.mouse.move(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
                await page.mouse.wheel(0, 600 if action == "scroll_down" else -600)
                return ActionResult(True, "dom")
            if action == "new_tab":
                fresh = await page.context.new_page()
                with contextlib.suppress(Exception):
                    await fresh.goto("edge://newtab/", timeout=8000)
                await fresh.bring_to_front()
                self._adopt(fresh)
                return ActionResult(True, "dom")
            if action in ("next_tab", "prev_tab"):
                tabs = [p for p in page.context.pages if not p.is_closed()]
                if len(tabs) < 2:
                    return ActionResult(True, "dom")  # nothing to switch to
                step = 1 if action == "next_tab" else -1
                target = tabs[(tabs.index(page) + step) % len(tabs)]
                await target.bring_to_front()
                self._adopt(target)
                return ActionResult(True, "dom")
            return ActionResult(False, "dom", f"unknown browser action: {action}", "unsupported")

        return await self._guard(run)

    def _adopt(self, page: Any) -> None:
        """Make this the tab we control from now on (and keep closing its ad popups)."""
        self._page_ref = page
        page.on("popup", lambda popup: asyncio.ensure_future(_close_quietly(popup)))

    async def close_ad_dom(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            for frame in page.frames:  # ad UIs sometimes live in child frames
                button = frame.locator(SKIP_AD_SELECTORS).first
                if await _safe_count(button):
                    await button.click(timeout=2000)
                    return ActionResult(True, "dom")
            return ActionResult(False, "dom", "no skip control found", "selector_timeout")

        return await self._guard(run)

    async def close_ad_js(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            ok = await page.evaluate(ALLOWLISTED_JS["skip_ad"])
            return ActionResult(
                bool(ok),
                "js",
                None if ok else "ad not skippable via JS",
                None if ok else "iframe_cross_origin",
            )

        return await self._guard(run, "js")

    async def fullscreen_dom(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            for frame in page.frames:  # the player is often inside a child frame
                button = frame.locator(FULLSCREEN_SELECTORS).first
                if not await _safe_count(button):
                    continue
                async with self._hold_origin(page):  # ad scripts hijack clicks on some players
                    try:
                        await button.click(timeout=2500)
                    except Exception:
                        await button.click(timeout=1500, force=True)
                return ActionResult(True, "dom")
            return ActionResult(False, "dom", "no fullscreen control found", "selector_timeout")

        return await self._guard(run)

    async def fullscreen_js(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            # requestFullscreen needs a user gesture; CDP can assert one.
            session = await page.context.new_cdp_session(page)
            try:
                await session.send(
                    "Runtime.evaluate",
                    {
                        "expression": f"({ALLOWLISTED_JS['request_fullscreen']})()",
                        "userGesture": True,
                    },
                )
            finally:
                await session.detach()
            # The call only starts the request; report what actually happened, not that no
            # exception was raised (a pending or rejected request is not a success).
            await asyncio.sleep(0.3)
            entered = bool(await page.evaluate("() => !!document.fullscreenElement"))
            if not entered and await page.evaluate(
                "() => { const a = document.activeElement;"
                " return !(a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)); }"
            ):
                # Nearly every player binds "f"; a real key event counts as a user gesture.
                await page.keyboard.press("f")
                await asyncio.sleep(0.4)
                entered = bool(await page.evaluate("() => !!document.fullscreenElement"))
            if entered:
                return ActionResult(True, "js")
            return ActionResult(False, "js", "fullscreen did not engage", "no_user_gesture")

        return await self._guard(run, "js")

    async def eval_allowlisted(self, name: str) -> ActionResult:
        if name not in ALLOWLISTED_JS:
            return ActionResult(False, "js", f"script {name!r} is not allowlisted")

        async def run(page: Any) -> ActionResult:
            ok = await page.evaluate(ALLOWLISTED_JS[name])
            return ActionResult(bool(ok), "js")

        return await self._guard(run, "js")

    # ------------------------------------------------------------------ state / recovery

    async def snapshot(self) -> BrowserSnapshot:
        try:
            page = await self._page()
            data = await page.evaluate(JS_SNAPSHOT)
            if data["is_playing"] is None:  # no <video> up here: the player may be in a child frame
                inner = await _first_in_frames(page, JS_VIDEO_STATE)
                if inner:
                    data = {**data, **inner}
            tabs = len([p for p in page.context.pages if not p.is_closed()])
        except Exception:
            return BrowserSnapshot(tab_count=0)
        return BrowserSnapshot(
            url=data["url"],
            title=data["title"],
            source=_source_for(data["url"]),  # type: ignore[arg-type]
            is_playing=data["is_playing"],
            is_fullscreen=data["is_fullscreen"],
            ad_showing=data["ad_showing"],
            position_s=data["position_s"],
            duration_s=data["duration_s"],
            thumbnail_url=data["thumbnail_url"],
            tab_count=tabs,
        )

    async def refocus(self) -> None:
        try:
            await (await self._page()).bring_to_front()
        except Exception:
            log.warning("refocus failed", exc_info=True)

    async def reload(self) -> None:
        try:
            await (await self._page()).reload(wait_until="domcontentloaded", timeout=15000)
        except Exception:
            log.warning("reload failed", exc_info=True)

    async def dismiss_overlays(self) -> None:
        try:
            page = await self._page()
            await page.keyboard.press("Escape")
            await self.close_ad_dom()
        except Exception:
            log.warning("dismiss_overlays failed", exc_info=True)
