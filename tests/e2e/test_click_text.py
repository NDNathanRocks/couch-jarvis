"""click_text against a real page: buttons, a dropdown showing "Season 1", and nested spans."""

from __future__ import annotations

import asyncio
from typing import Any

from executors.edge_cdp import EdgeCDP

HTML = """
<body style="font:16px sans-serif">
<h2>Show</h2>
<select id="season" onchange="document.getElementById('out').textContent='picked '+this.value">
  <option value="1" selected>Season 1</option><option value="2">Season 2</option>
</select>
<div class="row"><span><b id="ep">Episode 3</b></span></div>
<button id="b" onclick="document.getElementById('out').textContent='clicked next'"><span>Next</span></button>
<a href="#" id="l" onclick="document.getElementById('out').textContent='clicked more';return false">Watch more videos</a>
<p>Season 1 of the show is great, said the critics at length.</p>
<div id="out"></div>
</body>
"""


async def _click(text: str) -> Any:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        apage = await browser.new_page()
        await apage.set_content(HTML)
        cdp = EdgeCDP("http://127.0.0.1:1")

        async def fake_page() -> Any:
            return apage

        cdp._page = fake_page  # type: ignore[method-assign]
        result = await cdp.click_text(text)
        out = await apage.inner_text("#out")
        await browser.close()
        return result, out


def test_dropdown_option_is_chosen_not_the_paragraph() -> None:
    result, out = asyncio.run(_click("Season 2"))
    assert result.ok and out == "picked 2"


def test_clicks_the_button_through_its_inner_span() -> None:
    result, out = asyncio.run(_click("Next"))
    assert result.ok and out == "clicked next"


def test_exact_text_beats_a_long_paragraph_mentioning_it() -> None:
    result, out = asyncio.run(_click("Watch more videos"))
    assert result.ok and out == "clicked more"


def test_missing_text_reports_so_the_screen_can_be_tried() -> None:
    result, _ = asyncio.run(_click("Pay now"))
    assert not result.ok and result.fallback_reason == "text_not_found"
