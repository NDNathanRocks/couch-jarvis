"""Fullscreen must hide Edge's own tab strip: window fullscreen plus the pointer out of the way."""

from __future__ import annotations

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
