"""Tell the user when a newer version of the app has been released.

Once a day the app asks GitHub's public "latest release" endpoint for the newest version number.
Nothing about this PC is sent, and nothing is downloaded or run: the phone and TV only show that a
newer version exists and where to get it. Turn it off with CJ_UPDATE_CHECK=false.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

import httpx

from version import __version__

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/NDNathanRocks/couch-jarvis/releases/latest"
CHECK_EVERY_S = 24 * 3600
RETRY_AFTER_S = 3600  # no internet yet, or GitHub unreachable
_TAG = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """'v0.3.1' / '0.3.1' -> (0, 3, 1); anything else (a pre-release tag, junk) -> None."""
    m = _TAG.match(text.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer_version(latest: str, current: str = __version__) -> str | None:
    """The cleaned-up latest version if it is newer than `current`, else None."""
    have, want = parse_version(current), parse_version(latest)
    if have is None or want is None or want <= have:
        return None
    return ".".join(str(n) for n in want)


async def fetch_latest(client: httpx.AsyncClient) -> str | None:
    """The newest released version number, or None when it cannot be read."""
    try:
        resp = await client.get(
            RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"CouchJarvis/{__version__}",
            },
        )
        resp.raise_for_status()
        tag = resp.json().get("tag_name", "")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return tag if isinstance(tag, str) else None


async def run_update_check(
    on_update: Callable[[str | None], None],
    *,
    client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], asyncio.Future[None] | asyncio.Task[None] | object] = asyncio.sleep,
) -> None:
    """Check now and then daily, forever. `on_update` gets the newer version or None."""
    http = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    try:
        while True:
            latest = await fetch_latest(http)
            if latest is None:
                wait = RETRY_AFTER_S
            else:
                found = newer_version(latest)
                if found:
                    log.info("a newer version (%s) has been released", found)
                on_update(found)
                wait = CHECK_EVERY_S
            await sleep(wait)  # type: ignore[misc]
    finally:
        if client is None:
            await http.aclose()
