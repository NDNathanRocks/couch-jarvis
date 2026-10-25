"""Taskbar tray icon: click to show or hide the pairing QR on the TV, with a small menu."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from brand import APP_NAME


def _icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7dd3fc"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("#0b1220"))
    font = QFont("Segoe UI", 24)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, APP_NAME[0])
    painter.end()
    return QIcon(pix)


class QrTray:
    def __init__(
        self,
        toggle: Callable[[], bool],
        reset: Callable[[], Awaitable[Any]],
        quit_app: Callable[[], None],
        open_app: Callable[[], None] | None = None,
    ) -> None:
        self._tray = QSystemTrayIcon(_icon())
        self._tray.setToolTip(f"{APP_NAME} - click to show or hide the QR code")
        menu = QMenu()
        self._open_action = QAction(f"Open {APP_NAME}...")
        self._open_action.triggered.connect(lambda: open_app() if open_app else None)
        self._open_action.setVisible(open_app is not None)
        self._toggle_action = QAction("Show / hide QR code")
        self._toggle_action.triggered.connect(lambda: toggle())
        self._reset_action = QAction("Reset QR code (old ones stop working)")
        self._reset_action.triggered.connect(lambda: _spawn(reset()))
        self._quit_action = QAction("Quit")
        self._quit_action.triggered.connect(lambda: quit_app())
        actions = (self._open_action, self._toggle_action, self._reset_action, self._quit_action)
        for action in actions:
            menu.addAction(action)
        self._menu = menu  # keep alive
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray.show()


def _spawn(coro: Awaitable[Any]) -> None:
    import asyncio

    asyncio.ensure_future(coro)
