"""The Control Center window: Status, Phones, Settings, Licence and Help (plan 5G).

Opened from the Start menu (a second launch of the app), the tray menu, or after the welcome
wizard. Closing it only hides it: the app keeps running in the tray.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from brand import APP_NAME, PRIVACY_URL, SUPPORT_URL, TAGLINE, TERMS_URL
from control import settings_file
from control.health import Row
from control.qt_common import STYLE, TONE_COLORS, ControlBackend, qr_pixmap, spawn

PAGES = ("Status", "Phones", "Settings", "Licence", "Help")


def _label(text: str, name: str | None = None, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        label.setObjectName(name)
    return label


def _button(text: str, name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        button.setObjectName(name)
    return button


def _page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    """A scrollable page with a title; returns it and the layout to fill."""
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(10)
    layout.addWidget(_label(title, "title"))
    if subtitle:
        layout.addWidget(_label(subtitle, "muted"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(inner)
    return scroll, layout


def _clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.setParent(None)  # gone at once, not when Qt gets round to deleting it
            widget.deleteLater()


class ControlWindow(QWidget):
    def __init__(self, backend: ControlBackend) -> None:
        super().__init__()
        self.backend = backend
        self.setWindowTitle(APP_NAME)
        self.setStyleSheet(STYLE)
        self.resize(900, 620)
        self.setMinimumSize(720, 480)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(180)
        self.nav.addItems(list(PAGES))
        self.stack = QStackedWidget()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.nav)
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._status_page())
        self.stack.addWidget(self._phones_page())
        self.stack.addWidget(self._settings_page())
        self.stack.addWidget(self._licence_page())
        self.stack.addWidget(self._help_page())
        self.nav.currentRowChanged.connect(self._on_page)
        self.nav.setCurrentRow(0)

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ window

    def open_page(self, name: str = "Status") -> None:
        self.nav.setCurrentRow(PAGES.index(name) if name in PAGES else 0)
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self._timer.start()
        self.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        event.ignore()
        self.hide()  # the app keeps running in the tray

    def _on_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.refresh()

    def refresh(self) -> None:
        page = PAGES[self.stack.currentIndex()]
        if page == "Status":
            spawn(self.backend.status(), self.show_status)
        elif page == "Phones":
            self.show_phones()
        elif page == "Licence":
            self.show_licence()

    # ------------------------------------------------------------------ status

    def _status_page(self) -> QWidget:
        page, layout = _page("Status", TAGLINE)
        self._status_box = QVBoxLayout()
        layout.addLayout(self._status_box)
        layout.addStretch(1)
        return page

    def show_status(self, rows: list[Row]) -> None:
        _clear(self._status_box)
        for row in rows:
            card = QFrame()
            card.setObjectName("card")
            line = QHBoxLayout(card)
            line.setContentsMargins(14, 10, 14, 10)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {TONE_COLORS[row.tone]}; font-size: 14pt;")
            line.addWidget(dot)
            name = _label(row.name, wrap=False)
            name.setMinimumWidth(130)
            name.setStyleSheet("font-weight: 600;")
            line.addWidget(name)
            line.addWidget(_label(row.text), 1)
            if row.fix:
                fix = _button(row.fix_label)
                fix.clicked.connect(lambda _=False, f=row.fix: self.run_fix(f))
                line.addWidget(fix)
            self._status_box.addWidget(card)

    def run_fix(self, fix: str) -> None:
        backend = self.backend
        if fix == "open_browser":
            spawn(backend.open_browser(), lambda _ok: self.refresh())
        elif fix == "reconnect":
            spawn(backend.reconnect(), lambda _r: self.refresh())
        elif fix in ("brain_key", "brain_setup"):
            self.open_page("Settings")
        elif fix in ("buy", "enter_key"):
            self.open_page("Licence")

    # ------------------------------------------------------------------ phones

    def _phones_page(self) -> QWidget:
        page, layout = _page(
            "Phones",
            "Every phone that scanned this TV's QR code. A guest gets media, volume and voice; "
            "Remove cuts a phone off.",
        )
        self._phones_box = QVBoxLayout()
        layout.addLayout(self._phones_box)
        layout.addWidget(_label("PAIR A NEW PHONE", "section"))
        pair = QFrame()
        pair.setObjectName("card")
        row = QHBoxLayout(pair)
        self._qr = QLabel()
        self._qr.setFixedSize(220, 220)
        row.addWidget(self._qr)
        row.addWidget(
            _label(
                "Scan this code with the phone's camera and open the link. The phone stays "
                "paired: closing the browser or locking the phone does not undo it.\n\n"
                "The same code also shows on the TV when you press the tray icon."
            ),
            1,
        )
        layout.addWidget(pair)
        layout.addStretch(1)
        return page

    def show_phones(self) -> None:
        _clear(self._phones_box)
        devices = sorted(self.backend.devices(), key=lambda d: (not d.online, d.name.lower()))
        if not devices:
            self._phones_box.addWidget(_label("No phones paired yet.", "muted"))
        for device in devices:
            card = QFrame()
            card.setObjectName("card")
            line = QHBoxLayout(card)
            line.setContentsMargins(14, 10, 14, 10)
            line.addWidget(_label(device.name or "Phone", wrap=False), 1)
            seen = (
                "Connected now"
                if device.online
                else f"Last seen {_ago(device.last_seen)}"
                if device.last_seen
                else "Not seen yet"
            )
            line.addWidget(_label(seen, "muted", wrap=False))
            guest = QCheckBox("Guest")
            guest.setToolTip(
                "Guests get media, volume and voice: no touchpad, keyboard or settings."
            )
            guest.setChecked(device.role == "guest")
            guest.toggled.connect(
                lambda on, d=device.device_id: self.backend.set_guest(d, bool(on))
            )
            line.addWidget(guest)
            remove = _button("Remove", "danger")
            remove.clicked.connect(
                lambda _=False, d=device.device_id, n=device.name: self.remove_phone(d, n)
            )
            line.addWidget(remove)
            self._phones_box.addWidget(card)
        url = self.backend.pairing_url()
        if url:
            self._qr.setPixmap(qr_pixmap(url))
        else:
            self._qr.setText("No pairing code yet")

    def remove_phone(self, device_id: str, name: str, *, confirm: bool = True) -> None:
        if confirm:
            answer = QMessageBox.question(
                self,
                "Remove phone",
                f"Remove {name or 'this phone'}? It stops working at once and must scan the "
                "QR code again to come back.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.backend.revoke(device_id)
        self.show_phones()

    # ------------------------------------------------------------------ settings

    def _settings_page(self) -> QWidget:
        page, layout = _page("Settings", "Changes apply after a quick restart of the app.")
        self.inputs: dict[str, QWidget] = {}
        self.errors: dict[str, QLabel] = {}
        values = settings_file.current(self.backend.config_path)
        section = None
        form: QFormLayout | None = None
        for field in settings_file.FIELDS:
            if field.name == "CJ_UPDATE_CHECK" and self.backend.channel == "store":
                continue  # the Store updates Store apps
            if field.section != section:
                section = field.section
                layout.addWidget(_label(section.upper(), "section"))
                card = QFrame()
                card.setObjectName("card")
                form = QFormLayout(card)
                form.setContentsMargins(16, 12, 16, 12)
                form.setHorizontalSpacing(18)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                layout.addWidget(card)
            assert form is not None
            widget = self._input(field, values[field.name])
            self.inputs[field.name] = widget
            error = _label("")
            error.setStyleSheet(f"color: {TONE_COLORS['bad']};")
            error.hide()
            self.errors[field.name] = error
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(widget)
            if field.help:
                box.addWidget(_label(field.help, "muted"))
            box.addWidget(error)
            holder = QWidget()
            holder.setObjectName("plain")
            holder.setLayout(box)
            form.addRow(_label(field.label), holder)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = _button("Save and restart", "primary")
        self.save_button.clicked.connect(lambda: self.save_settings())
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.saved_note = _label("", "muted")
        layout.addWidget(self.saved_note)
        layout.addStretch(1)
        return page

    def _input(self, field: settings_file.Field, value: str) -> QWidget:
        if field.kind == "bool":
            check = QCheckBox()
            check.setChecked(value == "true")
            return check
        if field.name == "CJ_HUD_SCREEN_INDEX":
            combo = QComboBox()
            for i, screen in enumerate(QGuiApplication.screens()):
                size = screen.size()
                combo.addItem(f"Screen {i + 1}: {screen.name()} ({size.width()}x{size.height()})")
            if combo.count() == 0:
                combo.addItem("Screen 1")
            combo.setCurrentIndex(min(int(float(value or 0)), combo.count() - 1))
            return combo
        if field.kind == "choice":
            combo = QComboBox()
            for v, text in field.choices:
                combo.addItem(text, v)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            return combo
        if field.kind == "number":
            spin = QDoubleSpinBox()
            spin.setDecimals(0)
            spin.setRange(field.minimum or 0, field.maximum or 1e6)
            spin.setValue(float(value or field.default))
            return spin
        edit = QLineEdit(value)
        if field.kind == "secret":
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("not set")
        return edit

    def form_values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, widget in self.inputs.items():
            if isinstance(widget, QCheckBox):
                out[name] = "true" if widget.isChecked() else "false"
            elif name == "CJ_HUD_SCREEN_INDEX" and isinstance(widget, QComboBox):
                out[name] = str(widget.currentIndex())
            elif isinstance(widget, QComboBox):
                out[name] = str(widget.currentData())
            elif isinstance(widget, QDoubleSpinBox):
                out[name] = str(int(widget.value()))
            elif isinstance(widget, QLineEdit):
                out[name] = widget.text().strip()
        return out

    def save_settings(self, *, restart: bool = True) -> list[str]:
        values = self.form_values()
        problems = {n: settings_file.validate(n, v) for n, v in values.items() if v}
        for name, label in self.errors.items():
            problem = problems.get(name)
            label.setText(problem or "")
            label.setVisible(bool(problem))
        if any(problems.values()):
            self.saved_note.setText("Fix the highlighted settings first.")
            return []
        changed = settings_file.save(self.backend.config_path, values)
        if not changed:
            self.saved_note.setText("Nothing changed.")
            return []
        self.saved_note.setText("Saved. Restarting the app...")
        if restart:
            QTimer.singleShot(300, self.backend.restart)
        return changed

    # ------------------------------------------------------------------ licence

    def _licence_page(self) -> QWidget:
        page, layout = _page("Licence")
        self._licence_note = _label("", "muted")
        self._licence_text = _label("")
        self._licence_text.setStyleSheet("font-size: 12pt;")
        layout.addWidget(self._licence_text)
        self.buy_button = _button(f"Buy {APP_NAME}", "primary")
        self.buy_button.clicked.connect(lambda: self._licence_note.setText(self.backend.buy()))
        row = QHBoxLayout()
        row.addWidget(self.buy_button)
        row.addStretch(1)
        layout.addLayout(row)
        self._key_box = QFrame()
        self._key_box.setObjectName("card")
        key_layout = QVBoxLayout(self._key_box)
        key_layout.addWidget(_label("Have a licence key? Paste it here."))
        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("ARMREST1....")
        key_row.addWidget(self.key_input, 1)
        activate = _button("Activate")
        activate.clicked.connect(lambda: self.activate_key())
        key_row.addWidget(activate)
        key_layout.addLayout(key_row)
        layout.addWidget(self._key_box)
        layout.addWidget(self._licence_note)
        layout.addStretch(1)
        return page

    def show_licence(self) -> None:
        lic = self.backend.licence()
        if lic.status == "active":
            who = f" to {lic.holder}" if lic.holder else ""
            text = (
                "Developer build: always licensed."
                if lic.source == "dev"
                else f"{APP_NAME} is licensed{who}. Thank you!"
            )
        elif lic.status == "trial":
            text = f"Free trial: {lic.days_left} days left."
        else:
            text = "Your free trial has ended. Buy a licence to keep using your phones as a remote."
        self._licence_text.setText(text)
        self.buy_button.setVisible(lic.status != "active")
        self._key_box.setVisible(not self.backend.uses_store() and lic.source != "key")

    def activate_key(self) -> bool:
        ok = self.backend.enter_key(self.key_input.text())
        self._licence_note.setText(
            "Activated. Thank you!" if ok else "That key is not valid. Check it was pasted whole."
        )
        if ok:
            self.key_input.clear()
            self.show_licence()
        return ok

    # ------------------------------------------------------------------ help

    def _help_page(self) -> QWidget:
        b = self.backend
        build = "Microsoft Store" if b.channel == "store" else b.channel
        page, layout = _page("Help", f"{APP_NAME} {b.version} ({build})")
        for text, action in (
            ("Get help", lambda: b.open_url(SUPPORT_URL)),
            ("Save a diagnostics file for support", self.save_diagnostics),
            ("Privacy policy", lambda: b.open_url(PRIVACY_URL)),
            ("Terms", lambda: b.open_url(TERMS_URL)),
            ("Third-party licences", self.open_notices),
        ):
            button = _button(text)
            button.clicked.connect(lambda _=False, a=action: a())
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self._help_note = _label("", "muted")
        layout.addWidget(self._help_note)
        layout.addWidget(_label("YOUR DATA", "section"))
        layout.addWidget(
            _label(
                "Your settings and paired phones are stored on this PC. The cloud relay only keeps "
                "this TV's room and its phones so they can reach it from anywhere."
            )
        )
        delete = _button("Delete my cloud data", "danger")
        delete.clicked.connect(lambda: self.delete_cloud())
        layout.addWidget(delete, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def save_diagnostics(self) -> Path:
        folder = Path.home() / "Documents"
        folder = folder if folder.is_dir() else Path.home()
        path = folder / f"{APP_NAME}-diagnostics-{dt.datetime.now():%Y%m%d-%H%M}.txt"
        text = self.backend.diagnostics()
        path.write_text(text, encoding="utf-8")
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._help_note.setText(f"Saved to {path} and copied. Nothing private is in it.")
        return path

    def open_notices(self) -> None:
        path = self.backend.notices_path()
        if path is None:
            self._help_note.setText(
                "The licence list is in THIRD_PARTY_NOTICES.txt in the app folder."
            )
        else:
            self.backend.open_url(path.as_uri())

    def delete_cloud(self, *, confirm: bool = True) -> None:
        if confirm:
            answer = QMessageBox.question(
                self,
                "Delete cloud data",
                "Delete this TV's room and its phones from the cloud relay? Every phone has to "
                "scan a new QR code afterwards. Settings on this PC are kept.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._help_note.setText("Deleting...")

        def done(problem: str | None) -> None:
            self._help_note.setText(
                f"Could not delete: {problem}"
                if problem
                else "Deleted. Restarting with a new room..."
            )

        spawn(self.backend.delete_cloud(), done)


def _ago(when: float) -> str:
    minutes = int(max(0.0, time.time() - when) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"
