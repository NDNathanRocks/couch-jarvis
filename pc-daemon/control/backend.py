"""The real ControlBackend: connects the Control Center to the running app's parts."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path

import lifecycle
from build_info import channel, is_packaged, runs_brain_installer
from config import Settings, resource_root, user_config_file
from contracts import DevicePresence
from control import diagnostics, settings_file
from control.health import Health, Row, rows
from licensing import LICENSED_DEV, Licence, LicenceManager
from version import __version__


class RuntimeBackend:
    def __init__(
        self,
        *,
        settings: Settings,
        service: object,
        health: Health,
        probe_browser: Callable[[], Awaitable[bool]],
        ensure_browser: Callable[[], Awaitable[bool]] | None,
        cloud: Callable[[], object | None],
        pairing_url: Callable[[], str | None],
        delete_cloud: Callable[[], Awaitable[str | None]] | None,
        log_dir: Path,
    ) -> None:
        from service import CommandService

        assert isinstance(service, CommandService)
        self._service = service
        self._settings = settings
        self._health = health
        self._probe = probe_browser
        self._ensure = ensure_browser
        self._cloud = cloud
        self._pairing_url = pairing_url
        self._delete_cloud = delete_cloud
        self._log_dir = log_dir
        self._last_rows: list[Row] = []
        self.version = __version__
        self.channel: str = channel()
        self.packaged = is_packaged()
        self.config_path = user_config_file()

    # ------------------------------------------------------------------ status

    def _manager(self) -> LicenceManager | None:
        gate = self._service.licence
        return gate if isinstance(gate, LicenceManager) else None

    async def status(self) -> list[Row]:
        lic = self.licence()
        cloud = self._cloud()
        relay = getattr(cloud, "status", None) if cloud is not None else None
        self._health.gemini_key = self._settings.gemini_api_key is not None
        devices = self._service.state.state.devices
        self._last_rows = rows(
            self._health,
            relay=relay,
            browser_ok=await self._probe(),
            licence_status=lic.status,
            licence_days=lic.days_left,
            licence_source=lic.source,
            phones_online=sum(1 for d in devices if d.online),
        )
        return self._last_rows

    def devices(self) -> list[DevicePresence]:
        return list(self._service.state.state.devices)

    def revoke(self, device_id: str) -> None:
        self._service.revoke_device(device_id)

    def set_guest(self, device_id: str, guest: bool) -> None:
        self._service.set_role(device_id, "guest" if guest else "owner")

    # ------------------------------------------------------------------ licence

    def licence(self) -> Licence:
        manager = self._manager()
        return manager.current if manager is not None else LICENSED_DEV

    def uses_store(self) -> bool:
        manager = self._manager()
        return manager.uses_store if manager is not None else False

    def enter_key(self, text: str) -> bool:
        manager = self._manager()
        if manager is None or manager.enter_key(text) is None:
            return False
        self._service.state.update(license=manager.state())
        return True

    def buy(self) -> str:
        manager = self._manager()
        return manager.buy() if manager is not None else "This build is always licensed."

    # ------------------------------------------------------------------ actions

    async def open_browser(self) -> bool:
        if self._ensure is None:
            return await self._probe()
        return await self._ensure()

    async def reconnect(self) -> None:
        reconnect = getattr(self._cloud(), "reconnect", None)
        if reconnect is not None:
            await reconnect()

    def brain_setup(self) -> str:
        if not runs_brain_installer():
            model = "phi4-mini"
            return (
                "Install Ollama from ollama.com, then open a terminal and run "
                f'"ollama pull {model}". Or use a free Gemini key instead.'
            )
        script = _brain_script()
        if script is None or sys.platform != "win32":
            return "The setup script is missing: reinstall the app, or use a Gemini key."
        subprocess.Popen(  # noqa: S603 - our own bundled script
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Pause",
            ],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return "The download runs in the new window. It can take a while; you can keep going."

    def pairing_url(self) -> str | None:
        return self._pairing_url()

    def restart(self) -> None:
        lifecycle.request_exit(lifecycle.EXIT_RESTART)

    async def delete_cloud(self) -> str | None:
        if self._delete_cloud is None:
            return "this PC is not connected to the cloud relay"
        return await self._delete_cloud()

    def diagnostics(self) -> str:
        secrets = [
            s.get_secret_value()
            for s in (
                self._settings.gemini_api_key,
                self._settings.groq_api_key,
                self._settings.elevenlabs_api_key,
                self._settings.license_key,
            )
            if s is not None
        ]
        return diagnostics.build(
            version=self.version,
            channel=self.channel,
            packaged=self.packaged,
            status=self._last_rows,
            settings=settings_file.read_values(self.config_path),
            logs={
                name: diagnostics.tail(self._log_dir / f"{name}.log") for name in ("daemon", "app")
            },
            secrets=secrets,
        )

    def open_url(self, url: str) -> None:
        if url.startswith("file:") and sys.platform == "win32":
            os.startfile(url)  # noqa: S606 - a file we ship
        else:
            webbrowser.open(url)

    def notices_path(self) -> Path | None:
        for candidate in (
            Path(sys.executable).parent / "THIRD_PARTY_NOTICES.txt",
            resource_root() / "installer" / "THIRD_PARTY_NOTICES.txt",
        ):
            if candidate.is_file():
                return candidate
        return None


def _brain_script() -> Path | None:
    for candidate in (
        Path(sys.executable).parent / "setup_brain.ps1",  # next to the installed app
        resource_root() / "scripts" / "setup_brain.ps1",  # a source checkout
    ):
        if candidate.is_file():
            return candidate
    return None
