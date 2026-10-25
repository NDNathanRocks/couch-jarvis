"""Opens the Control Center window or the welcome wizard, creating them on first use."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from control.qt_common import ControlBackend
from control.ui_state import UiState, needs_welcome

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from control.window import ControlWindow
    from control.wizard import WelcomeWizard


class ControlCenter:
    def __init__(self, backend: ControlBackend, ui_state: UiState) -> None:
        self.backend = backend
        self.ui_state = ui_state
        self.window: ControlWindow | None = None
        self.wizard: WelcomeWizard | None = None

    def open(self, page: str = "Status") -> None:
        if self.wizard is not None and self.wizard.isVisible():
            self.wizard.raise_()
            self.wizard.activateWindow()
            return
        if self.window is None:
            from control.window import ControlWindow

            self.window = ControlWindow(self.backend)
        self.window.open_page(page)
        log.info("control center opened (%s)", page)

    def welcome(self) -> None:
        from control.wizard import WelcomeWizard

        self.wizard = WelcomeWizard(self.backend, on_finished=self.ui_state.mark_setup_done)
        self.wizard.show()
        log.info("welcome wizard shown (fresh install)")

    def welcome_if_needed(self, room_age_s: float) -> bool:
        if not needs_welcome(self.ui_state, len(self.backend.devices()), room_age_s):
            if not self.ui_state.setup_done:
                self.ui_state.mark_setup_done()  # set up before the wizard existed
            return False
        self.welcome()
        return True
