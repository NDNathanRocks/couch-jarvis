"""The daily "a newer release exists" notice: never downloads or runs anything."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import update_check
from update_check import RELEASES_API, fetch_latest, newer_version, parse_version, run_update_check


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v0.3.1", (0, 3, 1)), ("0.10.0", (0, 10, 0)), (" v1.2.3 ", (1, 2, 3)), ("v1.2", None),
     ("v1.2.3-beta", None), ("latest", None), ("", None), ("v1.2.3; rm -rf /", None)],
)  # fmt: skip
def test_only_plain_release_numbers_are_understood(
    text: str, expected: tuple[int, int, int] | None
) -> None:
    assert parse_version(text) == expected


def test_newer_means_numerically_newer() -> None:
    assert newer_version("v0.3.1", "0.3.0") == "0.3.1"
    assert newer_version("v0.10.0", "0.9.9") == "0.10.0"  # not a string comparison
    assert newer_version("v0.3.0", "0.3.0") is None
    assert newer_version("v0.2.0", "0.3.0") is None
    assert newer_version("v1.0.0-rc1", "0.3.0") is None  # pre-releases are ignored


def client_returning(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_reads_the_tag_and_sends_nothing_about_this_pc() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"tag_name": "v0.9.0"})

    async with client_returning(handler) as client:
        assert await fetch_latest(client) == "v0.9.0"
    request = seen[0]
    assert str(request.url) == RELEASES_API and request.method == "GET" and not request.content
    assert set(request.headers) <= {"accept", "user-agent", "host", "accept-encoding", "connection"}


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404), httpx.Response(500), httpx.Response(200, text="not json"),
     httpx.Response(200, json={"tag_name": 5}), httpx.Response(200, json=["x"])],
)  # fmt: skip
async def test_a_bad_answer_is_just_no_answer(response: httpx.Response) -> None:
    async with client_returning(lambda r: response) as client:
        assert await fetch_latest(client) is None


async def test_unreachable_github_is_not_an_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with client_returning(boom) as client:
        assert await fetch_latest(client) is None


async def test_the_loop_reports_then_waits_a_day_and_retries_sooner_when_offline() -> None:
    answers = iter([None, "v9.0.0", "v0.0.1"])
    waits: list[float] = []
    reported: list[str | None] = []

    async def fake_fetch(client: Any) -> str | None:
        return next(answers)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            raise asyncio.CancelledError

    original = update_check.fetch_latest
    update_check.fetch_latest = fake_fetch  # type: ignore[assignment]
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_update_check(reported.append, client=httpx.AsyncClient(), sleep=sleep)
    finally:
        update_check.fetch_latest = original
    assert waits == [
        update_check.RETRY_AFTER_S,
        update_check.CHECK_EVERY_S,
        update_check.CHECK_EVERY_S,
    ]
    assert reported == [
        "9.0.0",
        None,
    ]  # offline reports nothing; an older "latest" clears the notice
