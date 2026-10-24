"""Which new tabs and redirects are fine: the ones a search page or the same site opens.

A tab that a search-results page opens is the user choosing a result, so it is allowed. A new tab
or redirect from any other site is blocked while ad blocking is on (streaming sites open ad pages
whenever their player is clicked).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_SEARCH_HOSTS = (
    r"(?:www\.)?google\.[a-z.]+",
    r"(?:www\.)?bing\.com",
    r"duckduckgo\.com",
    r"(?:[a-z]+\.)?search\.yahoo\.com",
    r"search\.brave\.com",
    r"(?:www\.)?ecosia\.org",
    r"(?:www\.)?startpage\.com",
    r"(?:www\.)?youtube\.com",
    r"music\.youtube\.com",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def site_of(url: str) -> str:
    """The registrable-ish site: ``a.b.example.com`` -> ``example.com`` (good enough here)."""
    parts = _host(url).split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_search_page(url: str) -> bool:
    """A page of search results: Google/Bing/DuckDuckGo/..., and YouTube's results page."""
    if url.startswith("edge://") or url.startswith("about:"):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(re.fullmatch(p, host) for p in _SEARCH_HOSTS):
        return False
    query = parse_qs(parsed.query)
    if host.endswith("youtube.com"):
        return (parsed.path == "/results" and "search_query" in query) or (
            parsed.path == "/search" and "q" in query
        )
    return bool(query.get("q") or query.get("p"))


def opens_freely(opener_url: str, target_url: str, *, adblock: bool) -> bool:
    """May a page at ``opener_url`` open / move to ``target_url`` without being blocked?"""
    if not adblock or is_search_page(opener_url):
        return True
    if not target_url.startswith(("http://", "https://")):
        return False  # a popup that is still about:blank is judged once it has loaded
    return site_of(opener_url) == site_of(target_url)
