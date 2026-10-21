"""Feeds the HUD view-model from the daemon's event bus (Qt-free, so it is testable)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from hud.viewmodel import HudViewModel
from service import CommandService

log = logging.getLogger(__name__)


class HudController:
    def __init__(
        self,
        service: CommandService,
        vm: HudViewModel,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self._service = service
        self._vm = vm
        self._on_change = on_change

    async def run(self) -> None:
        # Seed with the current state so a HUD started late is not blank until the next patch.
        self._vm.handle_event("state.snapshot", self._service.state.state.model_dump())
        self._on_change()
        async with self._service.bus.subscribe() as queue:
            while True:
                event = await queue.get()
                if self._service.bus.consume_lag(queue):
                    self._vm.handle_event("state.snapshot", self._service.state.state.model_dump())
                try:
                    payload = json.loads(event.data)["payload"]
                    self._vm.handle_event(event.type, payload)
                except (ValueError, KeyError, TypeError):
                    log.warning("HUD ignored a malformed %s event", event.type)
                    continue
                self._on_change()
