"""Translucent, always-on-top, click-through TV overlay (PySide6).

Draws the pairing QR, toasts, timers and a status pill over whatever is on screen without ever
taking focus or mouse input. All content decisions live in HudViewModel; this file only paints.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from hud.qr import qr_matrix
from hud.viewmodel import HudFrame, HudViewModel, QrCard, ToastItem

TONES = {"good": QColor(52, 211, 153), "warn": QColor(251, 191, 36), "bad": QColor(251, 113, 133)}
TOAST_COLORS = {
    "ok": QColor(5, 150, 105, 235),
    "error": QColor(225, 29, 72, 235),
    "info": QColor(39, 39, 42, 235),
}
PILL_BG = QColor(11, 13, 18, 205)
WHITE = QColor(255, 255, 255)
INK = QColor(8, 10, 14)

# QR card geometry in px at 1080p (scaled by screen height). Big on purpose: it must scan from the
# sofa, and it disappears as soon as someone is paired.
QR_CARD = 420
QR_MARGIN = 48
QR_PAD = 24
QR_CAPTION = 60


class TvOverlay(QWidget):
    def __init__(self, vm: HudViewModel, screen_index: int = 0) -> None:
        super().__init__()
        self._vm = vm
        self._frame: HudFrame = vm.frame()
        self._matrices: dict[str, list[list[bool]]] = {}
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("Couch Jarvis HUD")
        self._place_on_screen(screen_index)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def _place_on_screen(self, index: int) -> None:
        screens = QGuiApplication.screens()
        screen = screens[index] if 0 <= index < len(screens) else QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

    # ------------------------------------------------------------------ state

    def refresh(self) -> None:
        """Pull the latest frame; repaint only when something changed."""
        frame = self._vm.frame()
        if frame != self._frame:
            self._frame = frame
            self.update()

    # ------------------------------------------------------------------ layout (shared with tests)

    def _scale(self) -> float:
        return max(0.5, self.height() / 1080)

    def _qr_card_rect(self) -> QRectF:
        s = self._scale()
        outer, margin, caption = QR_CARD * s, QR_MARGIN * s, QR_CAPTION * s
        return QRectF(
            self.width() - margin - outer,
            self.height() - margin - outer - caption,
            outer,
            outer + caption,
        )

    def qr_layout(self) -> tuple[QRect, int] | None:
        """Rect (in widget pixels) that the QR modules occupy, and the pixel size of one module."""
        card = self._frame.qr
        if card is None:
            return None
        n = len(self._matrix(card.url))
        s = self._scale()
        outer, pad = int(QR_CARD * s), int(QR_PAD * s)
        area = outer - 2 * pad
        module = max(1, area // n)
        size = module * n
        rect = self._qr_card_rect()
        x = int(rect.x()) + pad + (area - size) // 2
        y = int(rect.y()) + pad + (area - size) // 2
        return QRect(x, y, size, size), module

    def _matrix(self, url: str) -> list[list[bool]]:
        if url not in self._matrices:
            self._matrices = {url: qr_matrix(url)}  # keep a single entry: the URL rarely changes
        return self._matrices[url]

    # ------------------------------------------------------------------ painting

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        s = self._scale()
        frame = self._frame
        self._paint_status(painter, frame, s)
        self._paint_toasts(painter, frame.toasts, s)
        self._paint_timers(painter, frame, s)
        if frame.qr is not None:
            self._paint_qr(painter, frame.qr, s)
        painter.end()

    def _font(self, px: float, bold: bool = False) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(max(8, int(px)))
        font.setBold(bold)
        return font

    def _paint_status(self, p: QPainter, frame: HudFrame, s: float) -> None:
        p.setFont(self._font(26 * s, bold=True))
        metrics = QFontMetrics(p.font())
        text_w = metrics.horizontalAdvance(frame.status)
        pad, dot = int(18 * s), int(14 * s)
        h = int(52 * s)
        x, y = int(36 * s), int(30 * s)
        w = pad * 2 + dot + int(12 * s) + text_w
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(PILL_BG)
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.setBrush(TONES[frame.tone])
        p.drawEllipse(QRectF(x + pad, y + (h - dot) / 2, dot, dot))
        p.setPen(WHITE)
        p.drawText(
            QRect(x + pad + dot + int(12 * s), y, text_w + 4, h),
            Qt.AlignmentFlag.AlignVCenter,
            frame.status,
        )
        if frame.progress:
            p.setFont(self._font(24 * s))
            p.setPen(QColor(186, 230, 253))
            p.drawText(x + pad, y + h + int(34 * s), frame.progress)

    def _paint_toasts(self, p: QPainter, toasts: tuple[ToastItem, ...], s: float) -> None:
        p.setFont(self._font(30 * s, bold=True))
        metrics = QFontMetrics(p.font())
        y = int(30 * s)
        for toast in toasts:
            w = min(int(self.width() * 0.6), metrics.horizontalAdvance(toast.text) + int(60 * s))
            h = int(64 * s)
            x = (self.width() - w) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(TOAST_COLORS[toast.kind])
            p.drawRoundedRect(QRectF(x, y, w, h), 16 * s, 16 * s)
            p.setPen(WHITE)
            shown = metrics.elidedText(toast.text, Qt.TextElideMode.ElideRight, w - int(40 * s))
            p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, shown)
            y += h + int(14 * s)

    def _paint_timers(self, p: QPainter, frame: HudFrame, s: float) -> None:
        y = int(30 * s)
        p.setFont(self._font(30 * s, bold=True))
        for row in frame.timers:
            text = f"{row.label}   {row.remaining}"
            w = QFontMetrics(p.font()).horizontalAdvance(text) + int(48 * s)
            h = int(56 * s)
            x = self.width() - int(36 * s) - w
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PILL_BG)
            p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
            p.setPen(QColor(125, 211, 252))
            p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
            y += h + int(12 * s)

    def _paint_qr(self, p: QPainter, card: QrCard, s: float) -> None:
        layout = self.qr_layout()
        if layout is None:
            return
        rect, module = layout
        card_rect = self._qr_card_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(WHITE)
        p.drawRoundedRect(card_rect, 24 * s, 24 * s)
        p.setBrush(INK)
        for r, row in enumerate(self._matrix(card.url)):
            for c, dark in enumerate(row):
                if dark:
                    p.drawRect(rect.x() + c * module, rect.y() + r * module, module, module)
        p.setPen(INK)
        p.setFont(self._font(26 * s, bold=True))
        caption = QRect(
            int(card_rect.x()),
            int(card_rect.bottom() - QR_CAPTION * s),
            int(card_rect.width()),
            int(QR_CAPTION * s),
        )
        p.drawText(caption, Qt.AlignmentFlag.AlignCenter, card.caption)
