"""Win32 DPI-safe input, media/volume keys, display sleep and sleep prevention.

Everything blocking runs in a worker thread so the event loop never stalls. The process must
call config.init_dpi_awareness() first so coordinates are physical pixels.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from collections.abc import Callable
from typing import Any

from agent.policy import key_allowed
from executors.base import ActionResult, MediaKey

log = logging.getLogger(__name__)

VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "mute": 0xAD,
    "vol_down": 0xAE,
    "vol_up": 0xAF,
}
KEYEVENTF_KEYUP = 0x0002
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_OFF = 2

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
MOUSE_ACTIONS: dict[str, tuple[int, ...]] = {
    "click": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "double_click": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) * 2,
    "right_click": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle_click": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    "drag_start": (MOUSEEVENTF_LEFTDOWN,),
    "drag_end": (MOUSEEVENTF_LEFTUP,),
}

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D


class _KeyboardInput(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _MouseInput(ctypes.Structure):  # only here so the INPUT union has its real (largest) size
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("ki", _KeyboardInput), ("mi", _MouseInput))


class _Input(ctypes.Structure):
    _fields_ = (("type", ctypes.c_ulong), ("u", _InputUnion))


def _send_key_events(events: list[tuple[int, int, int]]) -> None:
    """events are (vk, scan, flags); sent in one SendInput call so they stay in order."""
    inputs = (_Input * len(events))()
    for slot, (vk, scan, flags) in zip(inputs, events, strict=True):
        slot.type = INPUT_KEYBOARD
        slot.u.ki = _KeyboardInput(vk, scan, flags, 0, None)
    ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(_Input))


def _send_unicode(ch: str) -> None:
    """Type one character regardless of keyboard layout (surrogate pairs for emoji)."""
    units = ch.encode("utf-16-le")
    codes = [int.from_bytes(units[i : i + 2], "little") for i in range(0, len(units), 2)]
    events: list[tuple[int, int, int]] = []
    for code in codes:
        events.append((0, code, KEYEVENTF_UNICODE))
        events.append((0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    _send_key_events(events)


def _send_vk(vk: int) -> None:
    _send_key_events([(vk, 0, 0), (vk, 0, KEYEVENTF_KEYUP)])


class WindowsOS:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsOS requires Windows")

    async def _run(self, fn: Callable[[], ActionResult]) -> ActionResult:
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            log.exception("os action failed")
            return ActionResult(False, "os", f"{type(exc).__name__}: {exc}")

    async def press_media_key(self, key: MediaKey) -> ActionResult:
        def press() -> ActionResult:
            user32 = ctypes.windll.user32
            user32.keybd_event(VK[key], 0, 0, 0)
            user32.keybd_event(VK[key], 0, KEYEVENTF_KEYUP, 0)
            return ActionResult(True, "os")

        return await self._run(press)

    def _endpoint(self) -> Any:
        from pycaw.pycaw import AudioUtilities

        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):  # pycaw >= 2024
            return speakers.EndpointVolume
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume

        iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))

    async def set_volume(self, level: int) -> ActionResult:
        def apply() -> ActionResult:
            ep = self._endpoint()
            ep.SetMute(0, None)
            ep.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100.0, None)
            return ActionResult(True, "os")

        return await self._run(apply)

    async def get_volume(self) -> tuple[int, bool]:
        def read() -> tuple[int, bool]:
            ep = self._endpoint()
            return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())

        return await asyncio.to_thread(read)

    async def click(self, x: int, y: int) -> ActionResult:
        def do_click() -> ActionResult:
            import pyautogui

            pyautogui.FAILSAFE = False  # corner-abort would strand a TV with no keyboard
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click()
            return ActionResult(True, "os", observed={"x": x, "y": y})

        return await self._run(do_click)

    async def move_to(self, x: int, y: int) -> ActionResult:
        def move() -> ActionResult:
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.moveTo(x, y)
            return ActionResult(True, "os")

        return await self._run(move)

    async def key_combo(self, *keys: str) -> ActionResult:
        if not key_allowed("+".join(keys)):  # second line of defense behind the agent policy
            return ActionResult(False, "os", "key combination is not allowed")

        def press() -> ActionResult:
            import pyautogui

            pyautogui.hotkey(*keys)
            return ActionResult(True, "os")

        return await self._run(press)

    # ---- phone touchpad / keyboard (policy is enforced by the caller: remote_input.py)

    async def move_rel(self, dx: int, dy: int) -> ActionResult:
        def move() -> ActionResult:
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)
            return ActionResult(True, "os")

        return await self._run(move)

    async def scroll(self, notches: int) -> ActionResult:
        def wheel() -> ActionResult:
            # Windows: positive delta = wheel away from you (up); the phone says positive = down
            ctypes.windll.user32.mouse_event(
                MOUSEEVENTF_WHEEL, 0, 0, -int(notches) * WHEEL_DELTA, 0
            )
            return ActionResult(True, "os")

        return await self._run(wheel)

    async def mouse_action(self, action: str) -> ActionResult:
        flags = MOUSE_ACTIONS.get(action)
        if flags is None:
            return ActionResult(False, "os", f"unknown mouse action: {action}")

        def act() -> ActionResult:
            user32 = ctypes.windll.user32
            for flag in flags:
                user32.mouse_event(flag, 0, 0, 0, 0)
            return ActionResult(True, "os")

        return await self._run(act)

    async def press_key(self, key: str, mods: tuple[str, ...]) -> ActionResult:
        def press() -> ActionResult:
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.hotkey(*mods, key)
            return ActionResult(True, "os")

        return await self._run(press)

    async def type_text(self, text: str) -> ActionResult:
        def type_() -> ActionResult:
            for ch in text:
                if ch == "\n":
                    _send_vk(VK_RETURN)
                else:
                    _send_unicode(ch)
            return ActionResult(True, "os")

        return await self._run(type_)

    async def sleep_display(self) -> ActionResult:
        def off() -> ActionResult:
            ctypes.windll.user32.SendMessageW(
                HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF
            )
            return ActionResult(True, "os")

        return await self._run(off)

    def keep_awake(self, enabled: bool) -> None:
        flags = ES_CONTINUOUS
        if enabled:
            flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
