"""Screen capture, Set-of-Mark grid overlay and VLM coordinate mapping.

The VLM sees a downscaled screenshot with a numbered grid and answers with a mark id and/or a
normalized bounding box. Normalized coordinates survive the downscale, and are converted back
to PHYSICAL virtual-screen pixels here (the process is per-monitor-DPI aware, so PyAutoGUI and
MSS both speak physical pixels).
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from agent.prompts import GROUNDING_SYSTEM, grounding_user_prompt
from agent.state import GroundingResult
from executors.base import Screen, Screenshot
from llm.base import LLMError, VisionLLM
from telemetry import Telemetry

log = logging.getLogger(__name__)

GRID_ROWS = 8
GRID_COLS = 12
VLM_MAX_WIDTH = 1280


class MssScreen:
    """Real capture of the primary monitor (or the whole virtual desktop) via MSS."""

    def __init__(self, monitor_index: int = 1) -> None:
        self._index = monitor_index

    def capture(self) -> Screenshot:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            mon = sct.monitors[min(self._index, len(sct.monitors) - 1)]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Screenshot(buf.getvalue(), mon["left"], mon["top"], img.width, img.height)


def mark_to_bbox(
    mark_id: int, rows: int = GRID_ROWS, cols: int = GRID_COLS
) -> tuple[float, float, float, float]:
    """Mark ids are 1-based, row-major. Returns the cell as a normalized bbox."""
    if not 1 <= mark_id <= rows * cols:
        raise ValueError(f"mark_id {mark_id} outside 1..{rows * cols}")
    r, c = divmod(mark_id - 1, cols)
    return (c / cols, r / rows, (c + 1) / cols, (r + 1) / rows)


def bbox_to_screen_point(
    bbox_norm: tuple[float, float, float, float], shot: Screenshot
) -> tuple[int, int]:
    """Center of a normalized bbox in physical virtual-screen pixels (origin may be negative)."""
    x0, y0, x1, y1 = bbox_norm
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"bbox out of range: {bbox_norm}")
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (shot.left + round(cx * shot.width), shot.top + round(cy * shot.height))


def overlay_grid(png: bytes, rows: int = GRID_ROWS, cols: int = GRID_COLS) -> bytes:
    """Downscale for the VLM and draw numbered Set-of-Mark cells."""
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > VLM_MAX_WIDTH:
        img = img.resize((VLM_MAX_WIDTH, round(img.height * VLM_MAX_WIDTH / img.width)))
    draw = ImageDraw.Draw(img, "RGBA")
    cw, ch = img.width / cols, img.height / rows
    for r in range(rows):
        for c in range(cols):
            x, y = c * cw, r * ch
            draw.rectangle([x, y, x + cw, y + ch], outline=(255, 0, 0, 160), width=1)
            draw.text((x + 3, y + 2), str(r * cols + c + 1), fill=(255, 255, 0, 230))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@dataclass(frozen=True)
class GroundedPoint:
    x: int
    y: int
    result: GroundingResult


class VisionGrounder:
    def __init__(self, vlm: VisionLLM, screen: Screen, telemetry: Telemetry) -> None:
        self._vlm = vlm
        self._screen = screen
        self._telemetry = telemetry

    async def ground(self, description: str) -> GroundedPoint | None:
        """Find a UI element on screen. Returns None if the VLM cannot find it or errs."""
        with self._telemetry.span("vlm.ground", attributes={"cj.target": description[:80]}) as span:
            shot = self._screen.capture()
            marked = overlay_grid(shot.png)
            try:
                reply = await self._vlm.complete_with_image(
                    GROUNDING_SYSTEM,
                    grounding_user_prompt(description, GRID_ROWS, GRID_COLS),
                    marked,
                )
            except LLMError as exc:
                span.set_error(exc)
                return None
            span.set_attribute("gen_ai.system", reply.system)
            span.set_attribute("gen_ai.request.model", reply.model)
            span.set_attribute("gen_ai.usage.input_tokens", reply.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", reply.output_tokens)
            try:
                result = GroundingResult.model_validate(json.loads(reply.text))
                if not result.found:
                    return None
                bbox = result.bbox_norm
                if bbox is None and result.mark_id is not None:
                    bbox = mark_to_bbox(result.mark_id)
                if bbox is None:
                    return None
                x, y = bbox_to_screen_point(bbox, shot)
            except (ValueError, ValidationError) as exc:
                log.warning("unusable grounding reply: %r", exc)
                span.set_error(exc)
                return None
            return GroundedPoint(x, y, result)
