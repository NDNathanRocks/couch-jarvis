"""Renders the real Qt overlay offscreen and checks the pixels (marker: gui)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hud.qr import qr_matrix  # noqa: E402
from hud.tv_overlay import TvOverlay  # noqa: E402
from hud.viewmodel import HudViewModel  # noqa: E402

URL = "https://pwa.example/?room=room-abcdef123&secret=abcdefghijklmnop"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def render(overlay: TvOverlay) -> QImage:
    overlay.resize(1920, 1080)
    overlay.refresh()
    image = QImage(overlay.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    overlay.render(image)
    return image


def make_overlay(qapp: QApplication, **kw: object) -> tuple[TvOverlay, HudViewModel]:
    vm = HudViewModel(cloud_url=lambda: URL, lan_url=lambda: None)
    overlay = TvOverlay(vm)
    return overlay, vm


def test_window_is_click_through_always_on_top_and_translucent(qapp: QApplication) -> None:
    overlay, _ = make_overlay(qapp)
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_qr_code_on_screen_decodes_back_to_the_pairing_url(qapp: QApplication) -> None:
    overlay, _ = make_overlay(qapp)
    image = render(overlay)
    layout = overlay.qr_layout()
    assert layout is not None
    rect, module = layout
    expected = qr_matrix(URL)
    assert rect.width() == module * len(expected)
    for r, row in enumerate(expected):
        for c, dark in enumerate(row):
            px = image.pixelColor(
                rect.x() + c * module + module // 2, rect.y() + r * module + module // 2
            )
            assert (px.red() < 80) == dark, f"module ({r},{c}) rendered wrong"
    # the quiet zone around the modules is white, as the QR spec requires
    assert image.pixelColor(rect.x() - 1, rect.y() - 1) == QColor(255, 255, 255)


def test_transparent_where_nothing_is_drawn_so_the_video_shows_through(qapp: QApplication) -> None:
    overlay, vm = make_overlay(qapp)
    vm.handle_event(
        "state.snapshot",
        {"devices": [{"device_id": "a", "name": "A", "online": True, "last_seen": 1}]},
    )
    vm.request_qr(0.0001)  # make sure no QR window is open
    image = render(overlay)
    center = image.pixelColor(960, 540)
    assert center.alpha() == 0
    assert image.pixelColor(1000, 900).alpha() == 0


def test_toasts_and_status_are_painted(qapp: QApplication) -> None:
    overlay, vm = make_overlay(qapp)
    blank = render(overlay)
    vm.handle_event("cmd.result", {"command_id": "c", "ok": True, "message": "Playing lofi"})
    shown = render(overlay)
    top_center = shown.pixelColor(960, 60)
    assert top_center.alpha() > 200 and top_center.green() > top_center.red()  # green "ok" toast
    assert blank.pixelColor(960, 60).alpha() == 0
    status_pill = shown.pixelColor(60, 37)
    assert status_pill.alpha() > 150  # the small dark status pill, top-left


def test_qr_disappears_once_a_friend_is_paired_and_the_linger_ends(qapp: QApplication) -> None:
    clock = [1000.0]
    vm = HudViewModel(
        cloud_url=lambda: URL, lan_url=lambda: None, clock=lambda: clock[0], qr_linger_s=10
    )
    overlay = TvOverlay(vm)
    assert render(overlay) is not None and overlay.qr_layout() is not None
    vm.handle_event(
        "state.patch",
        {"devices": [{"device_id": "a", "name": "A", "online": True, "last_seen": 1}], "rev": 2},
    )
    clock[0] += 11
    render(overlay)
    assert overlay.qr_layout() is None


def test_scales_with_the_screen_height(qapp: QApplication) -> None:
    overlay, _ = make_overlay(qapp)
    overlay.resize(1920, 1080)
    overlay.refresh()
    big = overlay.qr_layout()
    overlay.resize(1280, 720)
    small = overlay.qr_layout()
    assert big is not None and small is not None
    assert small[0].width() < big[0].width()
    assert small[0].right() <= 1280 and small[0].bottom() <= 720


def test_tray_icon_builds_and_its_actions_call_through() -> None:
    from hud.tray import QrTray

    QApplication.instance() or QApplication([])
    calls: list[str] = []

    async def reset() -> None:
        calls.append("reset")

    tray = QrTray(lambda: calls.append("toggle") or True, reset, lambda: calls.append("quit"))
    tray._toggle_action.trigger()
    tray._quit_action.trigger()
    assert calls == ["toggle", "quit"]


def test_the_status_pill_fades_away_when_nothing_happens_and_returns_on_activity(
    qapp: QApplication,
) -> None:
    clock = [1000.0]
    vm = HudViewModel(cloud_url=lambda: URL, lan_url=lambda: None, clock=lambda: clock[0])
    overlay = TvOverlay(vm)
    assert render(overlay).pixelColor(60, 37).alpha() > 150  # shown at first
    clock[0] += 60  # a quiet minute: the movie is not covered by a badge
    assert render(overlay).pixelColor(60, 37).alpha() == 0
    vm.touch()
    assert render(overlay).pixelColor(60, 37).alpha() > 150
