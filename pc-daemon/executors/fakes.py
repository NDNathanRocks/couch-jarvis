"""In-memory fakes of Browser / Screen / OSController for tests and --dry-run."""

from __future__ import annotations

import io

from executors.base import (
    ActionResult,
    BrowserSnapshot,
    MediaCommand,
    MediaKey,
    Screenshot,
)
from executors.page_items import ITEM_NOT_FOUND, NO_SEARCH_BAR, Candidate, pick_item


def blank_png(width: int = 1920, height: int = 1080) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (20, 20, 20)).save(buf, format="PNG")
    return buf.getvalue()


class FakeBrowser:
    """Scriptable browser. Set *_ok flags / failure reasons to exercise each tier."""

    def __init__(self) -> None:
        self.snap = BrowserSnapshot(url="https://www.youtube.com/", title="YouTube")
        self.calls: list[str] = []
        self.items: list[str] = []  # clickable things "on the page", in DOM order
        self.wakes = 0  # times the player controls were woken (mouse wiggle)
        self.search_bar = True  # the page has a search bar
        self.visible_text: set[str] = set()  # words click_text can find on the page
        self.subtitle_dom_fail = False  # no usable subtitles control in the DOM
        # failure scripting
        self.dom_fail_reason: str | None = None  # applies to click/fullscreen/close_ad dom tier
        self.js_ok = False
        self.search_ok = True
        self.playing_after_search = True
        self.park_spot: tuple[int, int] | None = (960, 800)
        self.can_seek = True  # a media element exists on the page
        self.fail_verify_times = 0  # number of play commands that do not take effect

    def _log(self, name: str) -> None:
        self.calls.append(name)

    async def navigate(self, url: str) -> ActionResult:
        self._log(f"navigate:{url}")
        self.snap = self.snap.model_copy(update={"url": url})
        return ActionResult(True, "dom")

    async def search_and_play(self, query: str, source: str, *, play: bool = True) -> ActionResult:
        self._log(f"search_and_play:{source}:{query}" if play else f"search:{source}:{query}")
        if not self.search_ok:
            return ActionResult(False, "dom", "no result", "selector_timeout")
        if not play:
            self.snap = self.snap.model_copy(
                update={"title": query, "source": "ytmusic" if source == "ytmusic" else "youtube"}
            )
            return ActionResult(True, "dom")
        self.snap = self.snap.model_copy(
            update={
                "title": query,
                "source": "ytmusic" if source == "ytmusic" else "youtube",
                "is_playing": self.playing_after_search,
            }
        )
        return ActionResult(True, "dom")

    async def list_items(self, limit: int = 40) -> list[Candidate]:
        return [Candidate(i, t) for i, t in enumerate(self.items[:limit])]

    async def click_item(
        self,
        *,
        index: int | None = None,
        text: str | None = None,
        episode: int | None = None,
        result: int | None = None,
    ) -> ActionResult:
        picked = {"index": index, "text": text, "episode": episode, "result": result}
        key, value = next((k, v) for k, v in picked.items() if v is not None)
        self._log(f"click_item:{key}={value}")
        if (
            self.items
            and pick_item(
                await self.list_items(), index=index, text=text, episode=episode, result=result
            )
            is None
        ):
            return ActionResult(False, "dom", ITEM_NOT_FOUND, "item_not_found")
        self.snap = self.snap.model_copy(update={"is_playing": True})
        return ActionResult(True, "dom")

    async def wake_controls(self) -> None:
        self.wakes += 1

    async def settle_fullscreen(self) -> tuple[int, int] | None:
        self._log("settle_fullscreen")
        return self.park_spot

    async def set_subtitles(self, state: str, lang: str) -> ActionResult:
        self._log(f"subtitles:{state}:{lang}")
        if self.subtitle_dom_fail:
            return ActionResult(False, "dom", "no subtitles control found", "no_subtitle_control")
        return ActionResult(True, "dom")

    async def open_saved(self, url: str) -> ActionResult:
        self._log(f"open_saved:{url}")
        self.snap = self.snap.model_copy(update={"url": url})
        return ActionResult(True, "dom")

    async def click_text(self, text: str) -> ActionResult:
        self._log(f"click_text:{text}")
        if text.lower() not in self.visible_text:
            return ActionResult(
                False, "dom", f"I can't see '{text}' on the page.", "text_not_found"
            )
        return ActionResult(True, "dom")

    async def open_search_tab(self, query: str, engine: str = "bing") -> ActionResult:
        self._log(
            f"open_search_tab:{query}" if engine == "bing" else f"open_search_tab:{engine}:{query}"
        )
        self.snap = self.snap.model_copy(
            update={"url": f"https://www.bing.com/search?q={query}", "title": f"{query} - Search"}
        )
        return ActionResult(True, "dom")

    async def page_search(self, query: str) -> ActionResult:
        self._log(f"page_search:{query}")
        if not self.search_bar:
            return ActionResult(False, "dom", NO_SEARCH_BAR, "no_search_bar")
        return ActionResult(True, "dom")

    async def click_selector(self, selector: str, timeout_ms: int) -> ActionResult:
        self._log(f"click_selector:{selector}")
        if self.dom_fail_reason:
            return ActionResult(False, "dom", "click failed", self.dom_fail_reason)
        return ActionResult(True, "dom")

    async def media_command(self, cmd: MediaCommand) -> ActionResult:
        self._log(f"media:{cmd}")
        if cmd in ("play", "pause", "play_pause"):
            if self.fail_verify_times > 0:
                self.fail_verify_times -= 1
                return ActionResult(True, "dom")  # reported ok but had no effect
            playing = {"play": True, "pause": False}.get(cmd, not bool(self.snap.is_playing))
            self.snap = self.snap.model_copy(update={"is_playing": playing})
        return ActionResult(True, "dom")

    async def seek(self, delta_s: int) -> ActionResult:
        self._log(f"seek:{delta_s}")
        return ActionResult(True, "dom")

    async def seek_to(self, position_s: float) -> ActionResult:
        self._log(f"seek_to:{position_s:g}")
        if not self.can_seek:
            return ActionResult(False, "dom", "no media element", "no_media")
        self.snap = self.snap.model_copy(update={"position_s": position_s})
        return ActionResult(True, "dom")

    async def browser_action(self, action: str) -> ActionResult:
        self._log(f"browser_nav:{action}")
        return ActionResult(True, "dom")

    async def close_tab(self) -> ActionResult:
        self._log("close_tab")
        self.snap = self.snap.model_copy(update={"tab_count": max(0, self.snap.tab_count - 1)})
        return ActionResult(True, "dom")

    async def close_ad_dom(self) -> ActionResult:
        self._log("close_ad_dom")
        if self.dom_fail_reason:
            return ActionResult(False, "dom", "skip button not found", self.dom_fail_reason)
        self.snap = self.snap.model_copy(update={"ad_showing": False})
        return ActionResult(True, "dom")

    async def close_ad_js(self) -> ActionResult:
        self._log("close_ad_js")
        if not self.js_ok:
            return ActionResult(False, "js", "js could not skip", "cross_origin_iframe")
        self.snap = self.snap.model_copy(update={"ad_showing": False})
        return ActionResult(True, "js")

    async def fullscreen_dom(self) -> ActionResult:
        self._log("fullscreen_dom")
        if self.dom_fail_reason:
            return ActionResult(False, "dom", "fullscreen button blocked", self.dom_fail_reason)
        self.snap = self.snap.model_copy(update={"is_fullscreen": True})
        return ActionResult(True, "dom")

    async def fullscreen_js(self) -> ActionResult:
        self._log("fullscreen_js")
        if not self.js_ok:
            return ActionResult(False, "js", "requestFullscreen rejected", "no_user_gesture")
        self.snap = self.snap.model_copy(update={"is_fullscreen": True})
        return ActionResult(True, "js")

    async def eval_allowlisted(self, name: str) -> ActionResult:
        self._log(f"eval:{name}")
        return ActionResult(True, "js")

    async def snapshot(self) -> BrowserSnapshot:
        return self.snap

    async def refocus(self) -> None:
        self._log("refocus")

    async def reload(self) -> None:
        self._log("reload")

    async def dismiss_overlays(self) -> None:
        self._log("dismiss_overlays")
        self.snap = self.snap.model_copy(update={"ad_showing": False})

    def apply_vision_click(self) -> None:
        """Called by FakeOS.click: a real click at the grounded point had its effect."""
        self.snap = self.snap.model_copy(update={"is_fullscreen": True, "ad_showing": False})


class FakeScreen:
    def __init__(self, width: int = 1920, height: int = 1080, left: int = 0, top: int = 0) -> None:
        self._shot = Screenshot(blank_png(width, height), left, top, width, height)
        self.captures = 0

    def capture(self) -> Screenshot:
        self.captures += 1
        return self._shot


class FakeOS:
    def __init__(self, browser: FakeBrowser | None = None) -> None:
        self.browser = browser
        self.volume = 50
        self.muted = False
        self.clicks: list[tuple[int, int]] = []
        self.parked: list[tuple[int, int]] = []
        self.keys: list[str] = []
        self.pointer_log: list[tuple[object, ...]] = []
        self.pressed: list[tuple[str, tuple[str, ...]]] = []
        self.typed: list[str] = []
        self.display_slept = False
        self.awake = False

    async def press_media_key(self, key: MediaKey) -> ActionResult:
        self.keys.append(key)
        if key == "vol_up":
            self.volume = min(100, self.volume + 5)
        elif key == "vol_down":
            self.volume = max(0, self.volume - 5)
        elif key == "mute":
            self.muted = not self.muted
        return ActionResult(True, "os")

    async def set_volume(self, level: int) -> ActionResult:
        self.volume, self.muted = max(0, min(100, level)), False
        return ActionResult(True, "os")

    async def get_volume(self) -> tuple[int, bool]:
        return self.volume, self.muted

    async def click(self, x: int, y: int) -> ActionResult:
        self.clicks.append((x, y))
        if self.browser is not None:
            self.browser.apply_vision_click()
        return ActionResult(True, "os")

    async def key_combo(self, *keys: str) -> ActionResult:
        self.keys.append("+".join(keys))
        return ActionResult(True, "os")

    async def move_to(self, x: int, y: int) -> ActionResult:
        self.parked.append((x, y))
        return ActionResult(True, "os")

    async def move_rel(self, dx: int, dy: int) -> ActionResult:
        self.pointer_log.append(("move", dx, dy))
        return ActionResult(True, "os")

    async def scroll(self, notches: int) -> ActionResult:
        self.pointer_log.append(("scroll", notches))
        return ActionResult(True, "os")

    async def mouse_action(self, action: str) -> ActionResult:
        self.pointer_log.append(("action", action))
        return ActionResult(True, "os")

    async def press_key(self, key: str, mods: tuple[str, ...]) -> ActionResult:
        self.pressed.append((key, mods))
        return ActionResult(True, "os")

    async def type_text(self, text: str) -> ActionResult:
        self.typed.append(text)
        return ActionResult(True, "os")

    async def sleep_display(self) -> ActionResult:
        self.display_slept = True
        return ActionResult(True, "os")

    def keep_awake(self, enabled: bool) -> None:
        self.awake = enabled
