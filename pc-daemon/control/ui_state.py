"""Small per-user facts about the window itself: has the welcome wizard been completed?"""

from __future__ import annotations

import json
from pathlib import Path


class UiState:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, object]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def setup_done(self) -> bool:
        return self._load().get("setup_done") is True

    def mark_setup_done(self) -> None:
        data = self._load()
        data["setup_done"] = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")


FRESH_ROOM_S = 24 * 3600


def needs_welcome(state: UiState, paired_phones: int, room_age_s: float) -> bool:
    """Show the wizard on a fresh install only. The phone list fills in only once the relay
    answers, so an older room (made by an earlier version, or a day ago) counts as set up."""
    return not state.setup_done and paired_phones == 0 and room_age_s < FRESH_ROOM_S
