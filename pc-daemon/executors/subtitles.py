"""Subtitles on any player: language names, and the vision tier for players with no usable DOM.

The DOM tiers live in EdgeCDP.set_subtitles (text tracks, YouTube's CC button, any control whose
label or title says subtitles/captions). When a player draws its own unlabelled icons, the
vision tier finds the CC icon on a screenshot, then the language entry in the menu it opens.
"""

from __future__ import annotations

import asyncio

from executors.base import ActionResult, Browser, OSController
from executors.vision_grounding import VisionGrounder

# ISO 639-1 code -> names a menu may show (lower case; native names because many players list
# languages in their own script). The first is the English name.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "en": ("english",),
    "es": ("spanish", "español", "espanol"),
    "fr": ("french", "français", "francais"),
    "de": ("german", "deutsch"),
    "it": ("italian", "italiano"),
    "pt": ("portuguese", "português", "portugues"),
    "nl": ("dutch", "nederlands"),
    "ru": ("russian", "русский"),
    "tr": ("turkish", "türkçe"),
    "ja": ("japanese", "日本語"),
    "ko": ("korean", "한국어"),
    "zh": ("chinese", "中文"),
    "hi": ("hindi", "हिन्दी"),
    "ar": ("arabic", "العربية"),
}

# What people say -> code ("spanish subtitles", "subtitles in french").
SPOKEN_LANGUAGES: dict[str, str] = {
    name: code for code, names in LANGUAGES.items() for name in names[:1]
} | {"english us": "en", "mexican spanish": "es"}


def language_names(code: str) -> tuple[str, ...]:
    """Menu labels to look for, plus the bare code ("en")."""
    return (*LANGUAGES.get(code, (code,)), code)


def language_name(code: str) -> str:
    return LANGUAGES.get(code, (code,))[0]


CC_TARGET = "the subtitles / closed captions (CC) button in the video player control bar"


class SubtitleController:
    """Tier 1-3 (DOM, text tracks) are the browser's; this adds the screenshot-based tier."""

    def __init__(
        self,
        browser: Browser,
        grounder: VisionGrounder,
        os_ctl: OSController,
        *,
        settle_s: float = 0.6,
    ) -> None:
        self._browser = browser
        self._grounder = grounder
        self._os = os_ctl
        self._settle_s = settle_s

    async def dom_tier(self, state: str, lang: str) -> ActionResult:
        return await self._browser.set_subtitles(state, lang)

    async def vision_tier(self, state: str, lang: str) -> ActionResult:
        await self._browser.wake_controls()  # players hide the control bar until the pointer moves
        if self._settle_s:
            await asyncio.sleep(self._settle_s)
        button = await self._grounder.ground(CC_TARGET)
        if button is None:
            return ActionResult(False, "vision", "no subtitles button found", "vlm_not_found")
        clicked = await self._os.click(button.x, button.y)
        if not clicked.ok:
            return ActionResult(False, "vision", clicked.error, "click_failed")
        if self._settle_s:
            await asyncio.sleep(self._settle_s)
        if state == "off":
            wanted = "the 'Off' option in the open subtitles menu"
        else:
            wanted = f"the {language_name(lang)} option in the open subtitles menu"
        entry = await self._grounder.ground(wanted)
        if entry is None:
            return ActionResult(False, "vision", "subtitle menu entry not found", "vlm_not_found")
        picked = await self._os.click(entry.x, entry.y)
        return ActionResult(picked.ok, "vision", picked.error)
