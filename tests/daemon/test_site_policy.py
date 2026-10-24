from __future__ import annotations

import pytest

from executors.site_policy import is_search_page, opens_freely, site_of


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/search?q=cats",
        "https://www.google.co.uk/search?q=cats",
        "https://www.bing.com/search?q=weather",
        "https://duckduckgo.com/?q=cats",
        "https://www.youtube.com/results?search_query=lofi",
        "https://music.youtube.com/search?q=lofi",
    ],
)
def test_search_result_pages(url: str) -> None:
    assert is_search_page(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/",
        "https://www.youtube.com/watch?v=abc",
        "https://example-stream.site/watch/1?q=x",
        "edge://newtab/",
        "about:blank",
    ],
)
def test_other_pages_are_not_search_results(url: str) -> None:
    assert not is_search_page(url)


def test_site_of_groups_subdomains() -> None:
    assert site_of("https://player.stream.example.com/x") == "example.com"
    assert site_of("https://www.bbc.co.uk/news") == "bbc.co.uk"


def test_policy() -> None:
    results = "https://www.google.com/search?q=cats"
    stream = "https://stream.example.com/watch/1"
    ad = "https://ads.badsite.xyz/offer"
    assert opens_freely(results, ad, adblock=True)  # the user picked a search result
    assert opens_freely(stream, "https://cdn.example.com/p", adblock=True)  # same site
    assert not opens_freely(stream, ad, adblock=True)  # a third-party popup
    assert not opens_freely(stream, "about:blank", adblock=True)  # judged after it loads
    assert opens_freely(stream, ad, adblock=False)  # the user turned blocking off
