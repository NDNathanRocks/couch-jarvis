"""Ad-ridden sites: popups must not steal control, clicks must not redirect the TV."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from executors.edge_cdp import EdgeCDP, _first_in_frames, _safe_count, blocks_navigation


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.handlers: dict[str, Any] = {}

    def is_closed(self) -> bool:
        return self.closed

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages


class FakeBrowser:
    def __init__(self, pages: list[FakePage]) -> None:
        self.contexts = [FakeContext(pages)]

    def is_connected(self) -> bool:
        return True


def cdp_with(pages: list[FakePage], shown: list[FakePage] | None = None) -> EdgeCDP:
    """An EdgeCDP over fake tabs. `shown` is Edge's tab list, the tab shown last first; it is
    read at each call, so a test can reorder it as a user switching tabs would."""
    cdp = EdgeCDP("http://127.0.0.1:9222")
    cdp._browser = FakeBrowser(pages)
    cdp._pw = object()
    order = shown if shown is not None else []

    async def recent() -> list[str]:
        return [f"t{id(p)}" for p in order]

    async def target_id(page: Any) -> str:
        return f"t{id(page)}"

    cdp._recent_tabs = recent  # type: ignore[method-assign]
    cdp._target_id = target_id  # type: ignore[method-assign]
    return cdp


async def test_stays_on_the_original_tab_when_an_ad_popup_opens() -> None:
    tv, ad = FakePage("https://shows.example/#/tv/1"), FakePage("https://casino.example/")
    cdp = cdp_with([tv, ad])
    assert await cdp._page() is tv  # oldest web page, not the newest one
    ctx = cdp._browser.contexts[0]
    ctx.pages.append(FakePage("https://another-ad.example/"))
    assert await cdp._page() is tv  # and it stays there


async def test_prefers_a_media_tab_over_other_tabs() -> None:
    other, yt = FakePage("https://shows.example/"), FakePage("https://www.youtube.com/watch?v=1")
    assert await cdp_with([other, yt])._page() is yt


async def test_popups_opened_by_the_tracked_tab_are_closed() -> None:
    tv = FakePage("https://shows.example/")
    cdp = cdp_with([tv])
    await cdp._page()
    popup = FakePage("https://casino.example/")
    tv.handlers["popup"](popup)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert popup.closed and not tv.closed


async def test_reselects_when_the_tracked_tab_is_closed() -> None:
    a, b = FakePage("https://a.example/"), FakePage("https://b.example/")
    cdp = cdp_with([a, b])
    assert await cdp._page() is a
    a.closed = True
    assert await cdp._page() is b


class FakeFrame:
    def __init__(self, result: Any = None, hang: bool = False, fail: bool = False) -> None:
        self._result, self._hang, self._fail = result, hang, fail

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if self._hang:
            await asyncio.sleep(3600)  # a frame whose script context never becomes ready
        if self._fail:
            raise RuntimeError("frame detached")
        return self._result


async def test_a_stuck_child_frame_cannot_stall_the_command() -> None:
    page = type("P", (), {})()
    page.frames = [FakeFrame(None), FakeFrame(hang=True), FakeFrame(fail=True), FakeFrame(True)]
    started = asyncio.get_running_loop().time()
    assert await _first_in_frames(page, "() => 1", frame_timeout_s=0.05) is True
    assert asyncio.get_running_loop().time() - started < 1


class FakeLocator:
    def __init__(self, count: int | None) -> None:
        self._count = count  # None = the frame never answers

    async def count(self) -> int:
        if self._count is None:
            await asyncio.sleep(3600)
        return self._count or 0


async def test_locator_count_on_a_dead_frame_is_zero_not_a_hang() -> None:
    assert await _safe_count(FakeLocator(None), timeout_s=0.05) == 0
    assert await _safe_count(FakeLocator(2), timeout_s=0.05) == 2


@pytest.mark.parametrize(
    ("start", "target", "blocked"),
    [
        ("https://shows.example/#/tv/1", "https://shows.example/#/tv/2", False),
        ("https://shows.example/a", "https://shows.example/b?x=1", False),
        ("https://shows.example/", "https://casino.example/", True),
        ("https://www.youtube.com/results", "https://www.youtube.com/watch?v=1", False),
        ("https://shows.example/", "about:blank", False),
        ("https://shows.example/", "data:text/html,hi", False),
        ("https://shows.example/", "https://shows.example.evil.example/", True),
    ],
)
def test_navigation_rule(start: str, target: str, blocked: bool) -> None:
    assert blocks_navigation(start, target) is blocked


# ---- follow the tab on the TV, not the first one (switching tabs by hand or with the touchpad)


class ShownPage(FakePage):
    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.context: Any = None
        self.fronted = 0

    async def bring_to_front(self) -> None:
        self.fronted += 1


async def test_commands_follow_the_tab_the_user_switched_to() -> None:
    first, second = ShownPage("https://www.youtube.com/watch?v=1"), ShownPage("https://b.example/")
    shown = [first, second]
    cdp = cdp_with([first, second], shown)
    assert await cdp._page() is first
    shown.reverse()  # the user clicked the other tab
    assert await cdp._page() is second
    assert cdp._page_ref is second  # and its popups are watched from now on
    assert "popup" in second.handlers


async def test_the_tab_on_screen_beats_a_media_tab_in_the_background() -> None:
    yt, docs = ShownPage("https://www.youtube.com/watch?v=1"), ShownPage("https://docs.example/")
    assert await cdp_with([yt, docs], [docs, yt])._page() is docs


async def test_keeps_the_tab_it_had_when_edge_cannot_say() -> None:
    a, b = ShownPage("https://a.example/"), ShownPage("https://b.example/")
    shown = [a, b]
    cdp = cdp_with([a, b], shown)
    assert await cdp._page() is a
    shown.clear()  # /json/list failed
    assert await cdp._page() is a


async def test_a_popup_being_judged_is_never_the_tab_we_control() -> None:
    tv = ShownPage("https://shows.example/")
    shown: list[FakePage] = [tv]
    cdp = cdp_with([tv], shown)
    await cdp._page()
    ad = ShownPage("https://casino.example/")
    cdp._browser.contexts[0].pages.append(ad)
    shown.insert(0, ad)  # Edge shows the ad tab for a moment
    cdp._judging.add(id(ad))
    assert await cdp._page() is tv


class TabContext:
    def __init__(self, pages: list[Any]) -> None:
        self.pages = pages

    async def new_page(self) -> Any:
        fresh = GotoPage()
        fresh.context = self
        self.pages.append(fresh)
        return fresh


class GotoPage(ShownPage):
    def __init__(self, url: str = "about:blank") -> None:
        super().__init__(url)
        self.visited: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)
        self.url = url


async def test_duplicate_tab_opens_the_same_page_and_shows_it() -> None:
    page = GotoPage("https://www.youtube.com/watch?v=abc")
    ctx = TabContext([page])
    page.context = ctx
    cdp = cdp_with([], [page])
    cdp._browser.contexts = [ctx]
    result = await cdp.browser_action("duplicate_tab")
    assert result.ok and len(ctx.pages) == 2
    copy = ctx.pages[1]
    assert copy.visited == ["https://www.youtube.com/watch?v=abc"] and copy.fronted == 1
    assert cdp._page_ref is copy
