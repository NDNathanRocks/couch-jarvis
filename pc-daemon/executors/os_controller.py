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

    async def key_combo(self, *keys: str) -> ActionResult:
        def press() -> ActionResult:
            import pyautogui

            pyautogui.hotkey(*keys)
            return ActionResult(True, "os")

        return await self._run(press)

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
