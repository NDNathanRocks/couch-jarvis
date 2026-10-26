"""Store screenshots of the phone remote (5K). Not part of the gate: it only runs on request.

    STORE_SHOTS=1 python -m pytest tests/e2e/test_store_screenshots.py -m e2e
    (run `npm run build` in web-remote first)

PNGs land in docs/store/. The TV overlay and the Control Center are windows on the real PC: capture
those by hand with Win+Shift+S (see docs/STORE_LISTING.md).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page

from e2e_support import Daemon, pair

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("STORE_SHOTS") != "1", reason="set STORE_SHOTS=1"),
]

OUT = Path(__file__).resolve().parents[2] / "docs" / "store"
SIZES = {"small": (360, 640), "medium": (390, 844), "large": (430, 932)}


@pytest.mark.parametrize("name", list(SIZES))
def test_phone_screenshots(phone: Callable[..., Page], daemon: Daemon, name: str) -> None:
    width, height = SIZES[name]
    page = phone({"width": width, "height": height})
    pair(page, daemon)
    OUT.mkdir(parents=True, exist_ok=True)
    page.get_by_label("Type a command").fill("play lofi on youtube")
    page.get_by_role("button", name="Send").click()
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / f"phone-remote-{name}.png"))
    page.get_by_role("button", name="Settings").click()
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"phone-settings-{name}.png"))
