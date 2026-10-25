"""The welcome wizard shown on a fresh install (plan 5G): five short steps, all skippable."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from brand import APP_NAME, GEMINI_KEY_URL, TAGLINE
from control import settings_file
from control.qt_common import STYLE, ControlBackend, qr_pixmap, spawn


def _text(text: str, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


class WelcomeWizard(QWizard):
    def __init__(self, backend: ControlBackend, on_finished: Callable[[], None]) -> None:
        super().__init__()
        self.backend = backend
        self._on_finished = on_finished
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setStyleSheet(STYLE)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.resize(720, 540)
        self._paired_at_start = len(backend.devices())
        for page in (
            self._welcome(),
            self._streaming(),
            self._brain(),
            self._privacy(),
            self._pair(),
        ):
            self.addPage(page)
        self.finished.connect(lambda _code: self.finish_setup())

    # ------------------------------------------------------------------ pages

    def _welcome(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(f"Welcome to {APP_NAME}")
        page.setSubTitle(TAGLINE)
        layout = QVBoxLayout(page)
        layout.addWidget(
            _text(
                f"{APP_NAME} turns any phone into a remote for this PC: voice, buttons, a touchpad "
                "and a keyboard, from the sofa or from anywhere. No accounts, no Wi-Fi setup. "
                "This takes about two minutes."
            )
        )
        layout.addWidget(_text("Which screen is your TV?"))
        self.screen_choice = QComboBox()
        for i, screen in enumerate(QGuiApplication.screens()):
            size = screen.size()
            self.screen_choice.addItem(
                f"Screen {i + 1}: {screen.name()} ({size.width()}x{size.height()})"
            )
        if self.screen_choice.count() == 0:
            self.screen_choice.addItem("Screen 1")
        current = settings_file.current(self.backend.config_path)["CJ_HUD_SCREEN_INDEX"]
        self.screen_choice.setCurrentIndex(min(int(float(current)), self.screen_choice.count() - 1))
        layout.addWidget(self.screen_choice)
        layout.addStretch(1)
        return page

    def _streaming(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Sign in to your streaming sites")
        page.setSubTitle(f"{APP_NAME} plays things in its own Microsoft Edge window on the TV.")
        layout = QVBoxLayout(page)
        layout.addWidget(
            _text(
                "Open the TV browser and sign in to the sites you watch (YouTube, Netflix, "
                "Spotify...). You only do this once: the sign-ins are kept."
            )
        )
        button = QPushButton("Open the TV browser")
        button.setObjectName("primary")
        self.browser_note = _text("", muted=True)
        button.clicked.connect(
            lambda: spawn(
                self.backend.open_browser(),
                lambda ok: self.browser_note.setText(
                    "Edge is open on the TV. Sign in there, then come back here."
                    if ok
                    else "Edge did not start. Is Microsoft Edge installed?"
                ),
            )
        )
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self.browser_note)
        layout.addStretch(1)
        return page

    def _brain(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Understanding what you say")
        page.setSubTitle("Buttons always work. Free-form commands need an AI.")
        layout = QVBoxLayout(page)
        self.brain_group = QButtonGroup(page)
        self.brain_note = _text("", muted=True)
        self.use_gemini = QRadioButton("Use a free Gemini key (fast, works on any PC)")
        self.use_local = QRadioButton("Run the AI on this PC (private; a 2-8 GB download)")
        self.use_none = QRadioButton("Skip for now")
        for i, radio in enumerate((self.use_gemini, self.use_local, self.use_none)):
            self.brain_group.addButton(radio, i)
            layout.addWidget(radio)
            if radio is self.use_gemini:
                row = QHBoxLayout()
                row.setContentsMargins(28, 0, 0, 6)
                self.gemini_key = QLineEdit()
                self.gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
                self.gemini_key.setPlaceholderText("Paste your key (starts with AIza)")
                get = QPushButton("Get a free key")
                get.clicked.connect(lambda: self.backend.open_url(GEMINI_KEY_URL))
                row.addWidget(self.gemini_key, 1)
                row.addWidget(get)
                layout.addLayout(row)
            if radio is self.use_local:
                row = QHBoxLayout()
                row.setContentsMargins(28, 0, 0, 6)
                self.download_button = QPushButton("Start the download")
                self.download_button.clicked.connect(
                    lambda: self.brain_note.setText(self.backend.brain_setup())
                )
                self.download_button.setEnabled(False)
                radio.toggled.connect(self.download_button.setEnabled)
                row.addWidget(self.download_button)
                row.addStretch(1)
                layout.addLayout(row)
        existing = settings_file.current(self.backend.config_path)["CJ_GEMINI_API_KEY"]
        self.gemini_key.setText(existing)
        (self.use_gemini if existing else self.use_none).setChecked(True)
        layout.addWidget(self.brain_note)
        layout.addStretch(1)
        return page

    def _privacy(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Your privacy")
        page.setSubTitle("Both are optional and can be changed later in Settings.")
        layout = QVBoxLayout(page)
        values = settings_file.current(self.backend.config_path)
        self.crash_reports = QCheckBox(
            "Send anonymous crash reports (only the error, never names or what you watch)"
        )
        self.crash_reports.setChecked(values["CJ_CRASH_REPORTS"] == "true")
        layout.addWidget(self.crash_reports)
        self.update_check = QCheckBox("Tell me when a new version is out")
        self.update_check.setChecked(values["CJ_UPDATE_CHECK"] == "true")
        self.update_check.setVisible(self.backend.channel != "store")
        layout.addWidget(self.update_check)
        layout.addStretch(1)
        return page

    def _pair(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Pair your phone")
        page.setSubTitle("Scan the code with your phone's camera and open the link.")
        layout = QHBoxLayout(page)
        self.qr = QLabel()
        self.qr.setFixedSize(240, 240)
        url = self.backend.pairing_url()
        if url:
            self.qr.setPixmap(qr_pixmap(url, 240))
        layout.addWidget(self.qr)
        side = QVBoxLayout()
        side.addWidget(
            _text(
                "Friends can scan it too and get their own remote. Then try saying "
                '"play lofi on YouTube" or press play.'
            )
        )
        self.pair_note = _text("Waiting for a phone...", muted=True)
        side.addWidget(self.pair_note)
        side.addStretch(1)
        layout.addLayout(side, 1)
        self._pair_timer = QTimer(page)
        self._pair_timer.setInterval(1000)
        self._pair_timer.timeout.connect(self.check_paired)
        self._pair_timer.start()
        return page

    def check_paired(self) -> bool:
        devices = self.backend.devices()
        if len(devices) > self._paired_at_start:
            self.pair_note.setText(f"Paired with {devices[-1].name or 'your phone'}. All set!")
            return True
        return False

    # ------------------------------------------------------------------ finish

    def choices(self) -> dict[str, str]:
        out = {
            "CJ_HUD_SCREEN_INDEX": str(self.screen_choice.currentIndex()),
            "CJ_CRASH_REPORTS": "true" if self.crash_reports.isChecked() else "false",
        }
        if self.backend.channel != "store":
            out["CJ_UPDATE_CHECK"] = "true" if self.update_check.isChecked() else "false"
        key = self.gemini_key.text().strip()
        if (
            self.use_gemini.isChecked()
            and key
            and settings_file.validate("CJ_GEMINI_API_KEY", key) is None
        ):
            out["CJ_GEMINI_API_KEY"] = key
        return out

    def finish_setup(self) -> list[str]:
        """Save what was chosen (also when the wizard is closed early), then restart if needed."""
        self._pair_timer.stop()
        changed = settings_file.save(self.backend.config_path, self.choices())
        self._on_finished()
        if changed:
            QTimer.singleShot(300, self.backend.restart)
        return changed
