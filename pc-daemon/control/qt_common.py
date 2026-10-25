"""Shared look and small helpers for the Control Center windows (Qt)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap

from contracts import DevicePresence
from control.health import Row
from hud.qr import qr_matrix
from licensing import Licence

log = logging.getLogger(__name__)

BG, PANEL, LINE, TEXT, MUTED = "#0b0d12", "#151922", "#252b38", "#e8eaf0", "#9aa3b5"
ACCENT, GOOD, WARN, BAD = "#0ea5e9", "#34d399", "#fbbf24", "#fb7185"
TONE_COLORS = {"good": GOOD, "warn": WARN, "bad": BAD}

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10.5pt; }}
QLabel, QCheckBox, QRadioButton, QWidget#plain {{ background: transparent; }}
QListWidget#nav {{ background: {PANEL}; border: none; padding: 8px; outline: 0; }}
QListWidget#nav::item {{ padding: 10px 14px; border-radius: 8px; margin: 2px 0; }}
QListWidget#nav::item:selected {{ background: {LINE}; color: {TEXT}; }}
QLabel#title {{ font-size: 17pt; font-weight: 600; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#section {{ color: {MUTED}; font-size: 9pt; font-weight: 600; letter-spacing: 1px;
                  margin-top: 14px; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QPushButton {{ background: {LINE}; border: none; border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ background: #303848; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; color: #04121b; font-weight: 600; }}
QPushButton#danger {{ background: transparent; color: {BAD}; border: 1px solid {BAD}; }}
QLineEdit, QComboBox, QDoubleSpinBox {{ background: {PANEL}; border: 1px solid {LINE};
    border-radius: 8px; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QCheckBox, QRadioButton {{ spacing: 10px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
    border: 2px solid {MUTED}; background: {PANEL}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT};
    border-color: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollArea {{ border: none; }}
QWizard, QWizardPage {{ background: {BG}; }}
"""


class ControlBackend(Protocol):
    """Everything the windows need from the running app (main.py provides the real one)."""

    version: str
    channel: str
    packaged: bool
    config_path: Path

    async def status(self) -> list[Row]: ...
    def devices(self) -> list[DevicePresence]: ...
    def revoke(self, device_id: str) -> None: ...
    def licence(self) -> Licence: ...
    def uses_store(self) -> bool: ...
    def enter_key(self, text: str) -> bool: ...
    def buy(self) -> str: ...
    async def open_browser(self) -> bool: ...
    async def reconnect(self) -> None: ...
    def brain_setup(self) -> str: ...
    def pairing_url(self) -> str | None: ...
    def restart(self) -> None: ...
    async def delete_cloud(self) -> str | None: ...
    def diagnostics(self) -> str: ...
    def open_url(self, url: str) -> None: ...
    def notices_path(self) -> Path | None: ...


def spawn[T](coro: Awaitable[T], done: Callable[[T], Any] | None = None) -> None:
    """Run a coroutine on the app's event loop (qasync) and hand its result back to the UI."""

    async def runner() -> None:
        try:
            result = await coro
        except Exception:  # noqa: BLE001 - a button must never take the app down
            log.warning("control center action failed", exc_info=True)
            return
        if done is not None:
            done(result)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no event loop (a test, or the app shutting down): nothing to run on
        close = getattr(coro, "close", None)
        if close is not None:
            close()
        return
    loop.create_task(runner())


def qr_pixmap(text: str, size: int = 220) -> QPixmap:
    matrix = qr_matrix(text)
    n = len(matrix)
    img = QImage(n, n, QImage.Format.Format_RGB32)
    dark, light = QColor(0, 0, 0).rgb(), QColor(255, 255, 255).rgb()
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            img.setPixel(x, y, dark if cell else light)
    return QPixmap.fromImage(img).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
    )
