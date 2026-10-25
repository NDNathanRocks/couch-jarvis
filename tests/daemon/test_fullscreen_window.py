"""Fullscreen must hide Edge's own tab strip: window fullscreen plus the pointer out of the way."""

from __future__ import annotations

from typing import Any

from executors.edge_cdp import EdgeCDP
from support import make_env


async def test_a_successful_fullscreen_settles_the_window_and_parks_the_pointer() -> None:
    env = make_env()
    result = await env.fullscreener.run("fullscreen")
    assert result.ok
    assert "settle_fullscreen" in env.browser.calls
    assert env.os.parked == [(960, 800)]


async def test_already_fullscreen_still_settles_it() -> None:
    env = make_env()
    env.browser.snap = env.browser.snap.model_copy(update={"is_fullscreen": True})
    result = await env.fullscreener.run("fullscreen")
    assert result.ok and "settle_fullscreen" in env.browser.calls and env.os.parked


async def test_nothing_is_parked_when_the_window_bounds_are_unknown() -> None:
    env = make_env()
    env.browser.park_spot = None
    assert (await env.fullscreener.run("fullscreen")).ok
    assert env.os.parked == []


async def test_closing_an_ad_does_not_touch_the_window() -> None:
    env = make_env()
    env.browser.snap = env.browser.snap.model_copy(update={"ad_showing": True})
    await env.fullscreener.run("close_ad")
    assert "settle_fullscreen" not in env.browser.calls and env.os.parked == []


# ---- the real Edge executor: undo the window fullscreen once the video leaves fullscreen


class _Session:
    def __init__(self, page: _Page) -> None:
        self.page = page

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.page.sent.append((method, params))
        if method == "Browser.getWindowForTarget":
            state = "fullscreen" if self.page.window_fs else "normal"
            bounds = {"left": 0, "top": 0, "width": 1920, "height": 1080, "windowState": state}
            return {"windowId": 7, "bounds": bounds}
        if method == "Browser.setWindowBounds" and params is not None:
            self.page.window_fs = params["bounds"]["windowState"] == "fullscreen"
        return {}

    async def detach(self) -> None:
        pass


class _Context:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.pages = [page]

    async def new_cdp_session(self, page: _Page) -> _Session:
        return _Session(page)


class _Page:
    url = "https://www.youtube.com/watch?v=1"

    def __init__(self, *, window_fs: bool = False) -> None:
        self.window_fs = window_fs
        self.video_fs = True
        self.sent: list[tuple[str, dict[str, Any] | None]] = []
        self.context = _Context(self)

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, script: str) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": "t",
            "is_playing": True,
            "is_fullscreen": self.video_fs,
            "ad_showing": False,
            "position_s": 0,
            "duration_s": 0,
            "thumbnail_url": None,
        }


def _edge(page: _Page) -> EdgeCDP:
    edge = EdgeCDP("http://127.0.0.1:9222")
    edge._page_ref = page
    edge._connect = _noop  # type: ignore[method-assign]
    return edge


async def _noop() -> None:
    pass


def _set_states(page: _Page) -> list[str]:
    return [
        p["bounds"]["windowState"] for m, p in page.sent if m == "Browser.setWindowBounds" and p
    ]


async def test_the_window_comes_back_when_the_video_leaves_fullscreen() -> None:
    page = _Page()
    edge = _edge(page)
    assert await edge.settle_fullscreen() == (960, 864)
    assert page.window_fs
    page.video_fs = False  # Esc, or the video ended
    await edge.snapshot()
    assert not page.window_fs and _set_states(page) == ["fullscreen", "normal"]
    await edge.snapshot()  # only once
    assert _set_states(page) == ["fullscreen", "normal"]


async def test_a_window_the_user_put_in_f11_mode_is_left_alone() -> None:
    page = _Page(window_fs=True)
    edge = _edge(page)
    await edge.settle_fullscreen()
    page.video_fs = False
    await edge.snapshot()
    assert page.window_fs and _set_states(page) == []


async def test_the_window_stays_fullscreen_while_the_video_is() -> None:
    page = _Page()
    edge = _edge(page)
    await edge.settle_fullscreen()
    await edge.snapshot()
    assert page.window_fs and _set_states(page) == ["fullscreen"]
