"""Self-healing Playwright-over-CDP manager for Microsoft Edge, YouTube and YouTube Music.

Only a fixed allowlist of JS snippets is ever evaluated; LLM output can name one but never
supply code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urlparse

from agent.policy import url_allowed
from executors.base import ActionResult, BrowserSnapshot, MediaCommand, Tier

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
      const el = document.querySelector('#movie_player') || document.querySelector('video');
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
            return self._page_ref
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        pages = [p for p in context.pages if not p.is_closed()]
        media = [p for p in pages if _source_for(p.url) != "other"]
        self._page_ref = (media or pages or [await context.new_page()])[-1]
        return self._page_ref

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

    async def search_and_play(self, query: str, source: str) -> ActionResult:
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
            await first.click(timeout=5000)
            await page.wait_for_selector("video", timeout=10000)
            await page.evaluate(ALLOWLISTED_JS["play"])
            return ActionResult(True, "dom")

        return await self._guard(go)

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
            ok = await page.evaluate(ALLOWLISTED_JS[name])
            return ActionResult(
                bool(ok),
                "dom",
                None if ok else "no media element",
                None if ok else "selector_timeout",
            )

        return await self._guard(run)

    async def seek(self, delta_s: int) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            ok = await page.evaluate(
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

    async def close_ad_dom(self) -> ActionResult:
        async def run(page: Any) -> ActionResult:
            for frame in page.frames:  # ad UIs sometimes live in child frames
                button = frame.locator(SKIP_AD_SELECTORS).first
                if await button.count():
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
            await page.locator(".ytp-fullscreen-button").first.click(timeout=3000)
            return ActionResult(True, "dom")

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
