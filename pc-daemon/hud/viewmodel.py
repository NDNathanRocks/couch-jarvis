"""Display-independent HUD logic: what the TV overlay should show right now.

Everything the overlay draws comes from ``HudViewModel.frame()``; it has no Qt dependency, so
the toast queue, QR policy, timers and status text are unit-testable without a display.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ToastKind = Literal["ok", "error", "info"]
Tone = Literal["good", "warn", "bad"]


@dataclass(frozen=True)
class ToastItem:
    text: str
    kind: ToastKind
    expires_at: float


@dataclass(frozen=True)
class TimerRow:
    label: str
    remaining: str


@dataclass(frozen=True)
class QrCard:
    url: str
    transport: Literal["cloud", "lan"]
    caption: str


@dataclass(frozen=True)
class HudFrame:
    qr: QrCard | None
    toasts: tuple[ToastItem, ...]
    timers: tuple[TimerRow, ...]
    status: str
    tone: Tone
    progress: str | None


def format_remaining(seconds: float) -> str:
    left = max(0, int(seconds + 0.999))
    h, rem = divmod(left, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class HudViewModel:
    def __init__(
        self,
        *,
        cloud_url: Callable[[], str | None],
        lan_url: Callable[[], str | None],
        clock: Callable[[], float] = time.time,
        toast_ttl_s: float = 5.0,
        max_toasts: int = 3,
        qr_linger_s: float = 10.0,
        qr_requested_s: float = 90.0,
    ) -> None:
        self._cloud_url = cloud_url
        self._lan_url = lan_url
        self._clock = clock
        self._ttl = toast_ttl_s
        self._max = max_toasts
        self._linger = qr_linger_s
        self._requested = qr_requested_s
        self._toasts: list[ToastItem] = []
        self._qr_until = 0.0
        self._pinned = False  # "show the QR" stays up until it is hidden or a friend connects
        self._suppressed = False  # hidden by hand before anyone paired
        self._ever_online = False  # once a phone has connected the QR never comes back by itself
        self._progress: dict[str, str] = {}
        # mirror of the PC's RoomState (only what the HUD needs)
        self._mode: Literal["cloud", "lan"] = "cloud"
        self._timers: list[dict[str, Any]] = []
        self._devices: dict[str, dict[str, Any]] = {}
        self._seen_state = False

    # ------------------------------------------------------------------ inputs

    def request_qr(self, seconds: float | None = None) -> None:
        """Show the pairing QR for a while (e.g. a friend asked to join)."""
        self._suppressed = False
        self._qr_until = max(self._qr_until, self._clock() + (seconds or self._requested))

    def show_qr(self) -> None:
        """Show the QR until it is hidden again or the next phone connects."""
        self._suppressed = False
        self._pinned = True

    def hide_qr(self) -> None:
        self._pinned = False
        self._suppressed = True
        self._qr_until = 0.0

    def qr_visible(self) -> bool:
        return self._wants_qr(self._clock())

    def toggle_qr(self) -> bool:
        """Flip the QR on the TV; returns whether it is now shown."""
        if self.qr_visible():
            self.hide_qr()
        else:
            self.show_qr()
        return self.qr_visible()

    def _wants_qr(self, now: float) -> bool:
        return self._pinned or now < self._qr_until or not (self._ever_online or self._suppressed)

    def toast(self, text: str, kind: ToastKind = "info", ttl_s: float | None = None) -> None:
        now = self._clock()
        item = ToastItem(text, kind, now + (ttl_s or self._ttl))
        self._toasts = [t for t in self._toasts if t.text != text] + [item]
        self._toasts = self._toasts[-self._max :]

    def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "state.snapshot":
            self._apply_state(payload, announce=self._seen_state)
            self._seen_state = True
        elif event_type == "state.patch":
            self._apply_state(payload, announce=True)
        elif event_type == "cmd.result":
            self._progress.pop(str(payload.get("command_id", "")), None)
            message = str(payload.get("message", "")).strip()
            if message:
                self.toast(message, "ok" if payload.get("ok") else "error")
        elif event_type == "agent.progress":
            node, status = str(payload.get("node", "")), str(payload.get("status", ""))
            if node == "timer" and status == "fired":
                self.toast(f"Timer done: {payload.get('detail', '')}".strip(), "info", ttl_s=10.0)
            elif status == "start":
                self._progress[str(payload.get("command_id", ""))] = _progress_label(node)

    def _apply_state(self, payload: dict[str, Any], *, announce: bool) -> None:
        if "mode" in payload and payload["mode"] in ("cloud", "lan"):
            self._mode = payload["mode"]
        if "timers" in payload:
            self._timers = list(payload["timers"])
        if "devices" in payload:
            before = {i for i, d in self._devices.items() if d.get("online")}
            self._devices = {d["device_id"]: d for d in payload["devices"]}
            after = {i for i, d in self._devices.items() if d.get("online")}
            if announce:
                for device_id in sorted(after - before):
                    self.toast(
                        f"{self._devices[device_id].get('name', 'A phone')} connected", "info"
                    )
            if after:
                if announce and after - before and self._wants_qr(self._clock()):
                    # someone just scanned it: show it worked for a moment, then clear the picture
                    self._pinned = False
                    self._qr_until = self._clock() + self._linger
                self._ever_online = True

    # ------------------------------------------------------------------ output

    @property
    def online_count(self) -> int:
        return sum(1 for d in self._devices.values() if d.get("online"))

    def _qr_card(self, now: float) -> QrCard | None:
        if not self._wants_qr(now):
            return None  # nobody is asking for it: keep the picture clear
        lan, cloud = self._lan_url(), self._cloud_url()
        # With the internet down only the LAN address can work, so it wins.
        if self._mode == "lan" and lan:
            return QrCard(lan, "lan", "Scan to pair · local network")
        if cloud:
            return QrCard(cloud, "cloud", "Scan to pair")
        if lan:
            return QrCard(lan, "lan", "Scan to pair · local network")
        return None

    def frame(self) -> HudFrame:
        now = self._clock()
        self._toasts = [t for t in self._toasts if t.expires_at > now]
        timers = tuple(
            TimerRow(
                str(t.get("label", "Timer")), format_remaining(float(t.get("ends_at", now)) - now)
            )
            for t in sorted(self._timers, key=lambda t: float(t.get("ends_at", 0)))
        )
        friends = self.online_count
        status: str
        tone: Tone
        if self._mode == "lan":
            status, tone = f"Local mode · {friends} connected", "warn"
        elif friends:
            status, tone = f"Online · {friends} connected", "good"
        else:
            status, tone = "Waiting for a phone", "warn"
        progress = next(reversed(self._progress.values()), None) if self._progress else None
        return HudFrame(self._qr_card(now), tuple(self._toasts), timers, status, tone, progress)


_PROGRESS = {
    "stt": "Listening…",
    "intent_router": "Thinking…",
    "dom_executor": "On it…",
    "vision_fallback": "Looking at the screen…",
    "post_action_verifier": "Checking…",
    "recovery_node": "Trying again…",
}


def _progress_label(node: str) -> str:
    return _PROGRESS.get(node, "Working…")
