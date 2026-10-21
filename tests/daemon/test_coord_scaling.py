from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from executors.base import Screenshot
from executors.fakes import FakeScreen, blank_png
from executors.vision_grounding import (
    GRID_COLS,
    GRID_ROWS,
    VisionGrounder,
    bbox_to_screen_point,
    mark_to_bbox,
    overlay_grid,
)
from llm.base import LLMError
from llm.fake import ScriptedLLM
from telemetry import Telemetry

LOGICAL_W, LOGICAL_H = 1920, 1080


def shot(scale: float, left: int = 0, top: int = 0) -> Screenshot:
    w, h = round(LOGICAL_W * scale), round(LOGICAL_H * scale)
    return Screenshot(b"", left, top, w, h)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75])
def test_bbox_center_lands_on_the_same_logical_spot_at_every_dpi(scale: float) -> None:
    # The target sits at 75% x / 50% y of the screen, whatever the Windows scale factor is.
    x, y = bbox_to_screen_point((0.70, 0.45, 0.80, 0.55), shot(scale))
    assert x == round(0.75 * LOGICAL_W * scale)
    assert y == round(0.50 * LOGICAL_H * scale)
    assert x / scale == pytest.approx(0.75 * LOGICAL_W, abs=1)  # logical position unchanged


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75])
def test_secondary_monitor_to_the_right_adds_its_origin(scale: float) -> None:
    primary_w = round(LOGICAL_W * scale)
    x, y = bbox_to_screen_point((0.0, 0.0, 1.0, 1.0), shot(scale, left=primary_w))
    assert x == primary_w + round(0.5 * primary_w)
    assert y == round(0.5 * LOGICAL_H * scale)


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_monitor_left_of_and_above_primary_has_negative_coordinates(scale: float) -> None:
    s = shot(scale, left=-round(LOGICAL_W * scale), top=-200)
    x, y = bbox_to_screen_point((0.25, 0.25, 0.75, 0.75), s)
    assert x == s.left + round(0.5 * s.width) < 0
    assert y == -200 + round(0.5 * s.height)


def test_mark_ids_are_row_major_and_one_based() -> None:
    assert mark_to_bbox(1) == (0.0, 0.0, 1 / GRID_COLS, 1 / GRID_ROWS)
    last = mark_to_bbox(GRID_ROWS * GRID_COLS)
    assert last[2] == pytest.approx(1.0) and last[3] == pytest.approx(1.0)
    x0, y0, _, _ = mark_to_bbox(GRID_COLS + 3)  # second row, third column (0-based)
    assert (x0, y0) == (2 / GRID_COLS, 1 / GRID_ROWS)
    for bad in (0, GRID_ROWS * GRID_COLS + 1, -3):
        with pytest.raises(ValueError):
            mark_to_bbox(bad)


@pytest.mark.parametrize(
    "bbox",
    [(0.5, 0.5, 0.5, 0.6), (0.6, 0.1, 0.4, 0.2), (-0.1, 0, 0.5, 0.5), (0, 0, 1.2, 0.5)],
)
def test_degenerate_or_out_of_range_boxes_are_rejected(
    bbox: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError):
        bbox_to_screen_point(bbox, shot(1.0))


def test_overlay_downscales_wide_screens_and_keeps_small_ones() -> None:
    big = Image.open(io.BytesIO(overlay_grid(blank_png(2880, 1620))))
    assert big.size == (1280, 720)
    small = Image.open(io.BytesIO(overlay_grid(blank_png(1000, 600))))
    assert small.size == (1000, 600)
    # the grid is really drawn: pixels differ from the flat input
    assert len({small.getpixel((x, 3)) for x in range(0, 1000, 7)}) > 1


def grounder(
    replies: list[str | Exception], screen: FakeScreen | None = None
) -> tuple[VisionGrounder, ScriptedLLM, Telemetry]:
    tele = Telemetry(None)
    vlm = ScriptedLLM(list(replies))
    return VisionGrounder(vlm, screen or FakeScreen(), tele), vlm, tele


async def test_grounding_prefers_bbox_over_mark() -> None:
    g, _, _ = grounder(
        [json.dumps({"found": True, "mark_id": 1, "bbox_norm": [0.8, 0.8, 0.9, 0.9]})]
    )
    p = await g.ground("fullscreen button")
    assert p is not None and (p.x, p.y) == (1632, 918)


async def test_grounding_falls_back_to_mark_center() -> None:
    g, _, _ = grounder([json.dumps({"found": True, "mark_id": GRID_COLS + 1})])
    p = await g.ground("x")
    assert p is not None
    assert (p.x, p.y) == (round(0.5 / GRID_COLS * 1920), round(1.5 / GRID_ROWS * 1080))


async def test_grounding_uses_physical_origin_of_the_captured_monitor() -> None:
    screen = FakeScreen(2560, 1440, left=-2560, top=0)
    g, _, _ = grounder([json.dumps({"found": True, "bbox_norm": [0.0, 0.0, 0.2, 0.2]})], screen)
    p = await g.ground("x")
    assert p is not None and (p.x, p.y) == (-2560 + 256, 144)


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps({"found": False}),
        "not json",
        json.dumps({"found": True}),  # neither bbox nor mark
        json.dumps({"found": True, "bbox_norm": [0.9, 0.9, 0.1, 0.1]}),
        json.dumps({"found": True, "mark_id": 999}),
        LLMError("vlm down"),
    ],
)
async def test_unusable_grounding_replies_return_none(reply: str | Exception) -> None:
    g, _, _ = grounder([reply])
    assert await g.ground("x") is None


async def test_grounding_records_token_usage_in_telemetry() -> None:
    g, _, tele = grounder([json.dumps({"found": True, "bbox_norm": [0.1, 0.1, 0.2, 0.2]})])
    await g.ground("x")
    (span,) = [r for r in tele.recent() if r["name"] == "vlm.ground"]
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert span["attributes"]["gen_ai.usage.output_tokens"] == 5
