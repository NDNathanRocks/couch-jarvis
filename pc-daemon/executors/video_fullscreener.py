"""3-tier ad-closer and fullscreen pipeline: DOM -> injected JS -> VLM grounding + PyAutoGUI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from executors.base import ActionResult, Browser, BrowserSnapshot, OSController
from executors.vision_grounding import VisionGrounder
from telemetry import Telemetry

log = logging.getLogger(__name__)

Op = Literal["fullscreen", "close_ad"]

VISION_TARGETS: dict[str, str] = {
    "fullscreen": "the fullscreen (expand) button in the video player control bar",
    "close_ad": "the 'Skip Ad' / 'Skip' button or the X close button of the advertisement overlay",
}


def _satisfied(op: Op, snap: BrowserSnapshot) -> bool:
    return snap.is_fullscreen if op == "fullscreen" else not snap.ad_showing


class VideoFullscreener:
    def __init__(
        self,
        browser: Browser,
        grounder: VisionGrounder,
        os_ctl: OSController,
        telemetry: Telemetry,
        *,
        settle_s: float = 0.4,
    ) -> None:
        self._browser = browser
        self._grounder = grounder
        self._os = os_ctl
        self._telemetry = telemetry
        self._settle_s = settle_s

    async def _verified(self, op: Op) -> bool:
        if self._settle_s:
            await asyncio.sleep(self._settle_s)
        return _satisfied(op, await self._browser.snapshot())

    async def _attempt(
        self, op: Op, tier: str, action: Callable[[], Awaitable[ActionResult]]
    ) -> ActionResult:
        with self._telemetry.span(
            "executor.step", attributes={"cj.tier": tier, "cj.op": op}
        ) as span:
            result = await action()
            if result.ok and not await self._verified(op):
                result = ActionResult(
                    False,
                    result.tier,
                    "action reported success but state unchanged",
                    result.fallback_reason or "no_effect",
                )
            if not result.ok:
                span.set_attribute("cj.fallback_reason", result.fallback_reason)
                span.set_error(result.error or "failed")
            return result

    async def dom_js_tiers(self, op: Op) -> ActionResult:
        """Tiers 1 and 2. A failure carries the DOM-tier (root cause) fallback reason."""
        if _satisfied(op, await self._browser.snapshot()):
            return ActionResult(True, "dom", observed={"already": True})
        dom = self._browser.fullscreen_dom if op == "fullscreen" else self._browser.close_ad_dom
        js = self._browser.fullscreen_js if op == "fullscreen" else self._browser.close_ad_js
        first = await self._attempt(op, "dom", dom)
        if first.ok:
            return first
        second = await self._attempt(op, "js", js)
        if second.ok:
            return second
        return ActionResult(
            False, "js", second.error, first.fallback_reason or second.fallback_reason
        )

    async def vision_tier(self, op: Op) -> ActionResult:
        """Tier 3: ground the control visually and click it with PyAutoGUI."""

        async def act() -> ActionResult:
            point = await self._grounder.ground(VISION_TARGETS[op])
            if point is None:
                return ActionResult(False, "vision", "target not found by VLM", "vlm_not_found")
            clicked = await self._os.click(point.x, point.y)
            return ActionResult(
                clicked.ok, "vision", clicked.error, observed={"x": point.x, "y": point.y}
            )

        return await self._attempt(op, "vision", act)

    async def run(self, op: Op) -> ActionResult:
        result = await self.dom_js_tiers(op)
        if result.ok:
            return result
        return await self.vision_tier(op)
