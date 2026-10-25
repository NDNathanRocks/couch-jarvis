"""The PC Control Center (plan 5G): settings file, status rows, diagnostics, and the real Qt
window and wizard driven offscreen with a fake backend."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contracts import DevicePresence  # noqa: E402
from control import diagnostics, settings_file  # noqa: E402
from control.health import Health, Row, rows  # noqa: E402
from control.ui_state import FRESH_ROOM_S, UiState, needs_welcome  # noqa: E402
from licensing import Licence  # noqa: E402

# ----------------------------------------------------------------------------- settings file

ORIGINAL = """# Armrest settings. Lines starting with # are ignored.
CJ_RELAY_WS_URL=wss://relay.example/prod
# CJ_GEMINI_API_KEY=...      free key
CJ_REMOTE_INPUT=true
CJ_SOMETHING_ELSE=keep me
"""


def test_saving_changes_only_the_settings_that_changed(tmp_path: Path) -> None:
    path = tmp_path / "config.env"
    path.write_text(ORIGINAL, encoding="utf-8")
    changed = settings_file.save(
        path,
        {
            "CJ_REMOTE_INPUT": "false",
            "CJ_GEMINI_API_KEY": "AIzaSyExample123",
            "CJ_SUBTITLE_LANG": "en",
        },
    )
    assert sorted(changed) == ["CJ_GEMINI_API_KEY", "CJ_REMOTE_INPUT"]  # "en" is the default
    text = path.read_text(encoding="utf-8")
    assert "# CJ_GEMINI_API_KEY=...      free key" in text  # comments survive
    assert "CJ_RELAY_WS_URL=wss://relay.example/prod" in text
    assert "CJ_SOMETHING_ELSE=keep me" in text
    assert "CJ_REMOTE_INPUT=false" in text and "CJ_REMOTE_INPUT=true" not in text
    assert settings_file.read_values(path)["CJ_GEMINI_API_KEY"] == "AIzaSyExample123"
    assert not list(tmp_path.glob(".config-*"))  # no temp file left behind


def test_emptying_a_setting_removes_it_and_duplicates_collapse(tmp_path: Path) -> None:
    path = tmp_path / "config.env"
    path.write_text(
        "CJ_GROQ_API_KEY=gsk_old12345\nCJ_GROQ_API_KEY=gsk_new12345\n", encoding="utf-8"
    )
    assert settings_file.current(path)["CJ_GROQ_API_KEY"] == "gsk_new12345"  # last wins
    assert settings_file.save(path, {"CJ_GROQ_API_KEY": ""}) == ["CJ_GROQ_API_KEY"]
    assert "CJ_GROQ_API_KEY" not in path.read_text(encoding="utf-8")


def test_values_with_spaces_are_quoted_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "config.env"
    settings_file.save(path, {"CJ_ELEVENLABS_API_KEY": "abc#def123456"})
    assert '"abc#def123456"' in path.read_text(encoding="utf-8")
    assert settings_file.read_values(path)["CJ_ELEVENLABS_API_KEY"] == "abc#def123456"


@pytest.mark.parametrize(
    ("name", "value", "ok"),
    [
        ("CJ_REMOTE_INPUT", "true", True),
        ("CJ_REMOTE_INPUT", "yes", False),
        ("CJ_STT_DEVICE", "cuda", True),
        ("CJ_STT_DEVICE", "tpu", False),
        ("CJ_HUD_BADGE_HIDE_S", "30", True),
        ("CJ_HUD_BADGE_HIDE_S", "-1", False),
        ("CJ_HUD_BADGE_HIDE_S", "soon", False),
        ("CJ_SUBTITLE_LANG", "fr", True),
        ("CJ_SUBTITLE_LANG", "French", False),
        ("CJ_GEMINI_API_KEY", "AIza12345678", True),
        ("CJ_GEMINI_API_KEY", "two words", False),
    ],
)
def test_each_setting_is_validated(name: str, value: str, ok: bool) -> None:
    assert (settings_file.validate(name, value) is None) is ok


def test_bad_values_and_unknown_settings_are_never_written(tmp_path: Path) -> None:
    path = tmp_path / "config.env"
    with pytest.raises(ValueError):
        settings_file.save(path, {"CJ_REMOTE_INPUT": "maybe"})
    with pytest.raises(KeyError):
        settings_file.save(path, {"CJ_RELAY_WS_URL": "wss://evil"})  # not offered in the window
    assert not path.exists()


# ----------------------------------------------------------------------------- status rows


def status(health: Health, **kw: object) -> dict[str, Row]:
    args: dict[str, object] = {
        "relay": "up",
        "browser_ok": True,
        "licence_status": "active",
        "licence_days": None,
        "licence_source": "store",
        "phones_online": 1,
    } | kw
    return {r.name: r for r in rows(health, **args)}  # type: ignore[arg-type]


def test_a_healthy_app_is_all_green() -> None:
    health = Health(brain_state="ready", brain_detail="Ready.", speech="ready")
    assert {r.tone for r in status(health).values()} == {"good"}


def test_each_problem_offers_its_fix() -> None:
    got = status(
        Health(brain_state="not_installed", speech="failed"),
        relay="down",
        browser_ok=False,
        licence_status="expired",
        licence_days=0,
        licence_source="trial",
        phones_online=0,
    )
    assert got["Internet relay"].fix == "reconnect"
    assert got["TV browser"].fix == "open_browser"
    assert got["AI brain"].fix == "brain_key" and "free-form" in got["AI brain"].text
    assert got["Voice"].tone == "bad"
    assert got["Licence"].fix == "buy"
    assert got["Phones"].tone == "warn"


def test_a_gemini_key_counts_as_a_working_brain() -> None:
    got = status(Health(brain_state="not_installed", gemini_key=True))
    assert got["AI brain"].tone == "good"


def test_trial_rows_say_the_days_left() -> None:
    got = status(Health(), licence_status="trial", licence_days=9, licence_source="trial")
    assert got["Licence"].text == "Free trial: 9 days left." and got["Licence"].fix == "enter_key"


# ----------------------------------------------------------------------------- diagnostics


def test_diagnostics_carry_no_secrets_or_private_details(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    log.write_text(
        "INFO opened https://www.netflix.com/watch/81 for C:\\Users\\Jane\\AppData\n"
        "INFO key AIzaSySecretSecret123 used\n",
        encoding="utf-8",
    )
    text = diagnostics.build(
        version="1.0.0",
        channel="store",
        packaged=True,
        status=[Row("TV browser", "good", "Microsoft Edge is ready.")],
        settings={"CJ_GEMINI_API_KEY": "AIzaSySecretSecret123", "CJ_REMOTE_INPUT": "true"},
        logs={"daemon": diagnostics.tail(log)},
        secrets=["AIzaSySecretSecret123"],
    )
    for leaked in ("AIzaSySecretSecret123", "netflix", "Jane"):
        assert leaked not in text, leaked
    assert "CJ_GEMINI_API_KEY: set" in text and "CJ_REMOTE_INPUT: true" in text
    assert "[good] TV browser" in text and "version 1.0.0 (store, Store package)" in text


# ----------------------------------------------------------------------------- the wizard rule


def test_the_wizard_shows_on_a_fresh_install_only(tmp_path: Path) -> None:
    state = UiState(tmp_path / "ui.json")
    assert needs_welcome(state, paired_phones=0, room_age_s=30)
    assert not needs_welcome(state, paired_phones=1, room_age_s=30)
    assert not needs_welcome(state, paired_phones=0, room_age_s=FRESH_ROOM_S + 1)  # 0.x user
    state.mark_setup_done()
    assert not needs_welcome(UiState(tmp_path / "ui.json"), paired_phones=0, room_age_s=30)


# ----------------------------------------------------------------------------- local API


async def test_the_window_opens_through_the_local_api_from_this_pc_only(env) -> None:  # type: ignore[no-untyped-def]
    from event_bus import EventBus
    from local_api import create_app
    from service import CommandService
    from timers import TimerService

    service = CommandService(
        room_id="r1",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda info: asyncio.sleep(0)),
        mode="lan",
    )
    app = create_app(service, env.telemetry)
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=local, base_url="http://127.0.0.1") as c:
        assert (await c.post("/api/ui/open")).status_code == 409  # no window (headless)
        opened: list[str] = []
        service.on_open_ui = opened.append
        assert (await c.post("/api/ui/open?page=Settings")).status_code == 200
        assert (await c.post("/api/ui/open?page=Evil")).status_code == 422
        assert opened == ["Settings"]
    remote = httpx.ASGITransport(app=app, client=("192.168.1.20", 5000))
    async with httpx.AsyncClient(transport=remote, base_url="http://192.168.1.10:8765") as c:
        assert (await c.post("/api/ui/open")).status_code == 403


# ----------------------------------------------------------------------------- the Qt window

pytest.importorskip("PySide6")


class FakeBackend:
    version = "1.0.0"
    channel = "direct"
    packaged = False

    def __init__(self, tmp_path: Path) -> None:
        self.config_path = tmp_path / "config.env"
        self.restarted = 0
        self.revoked: list[str] = []
        self.opened: list[str] = []
        self.lic = Licence("trial", "trial", 5)
        self.phones = [
            DevicePresence(
                device_id="d1", name="Jane's iPhone", online=True, last_seen=time.time()
            ),
            DevicePresence(
                device_id="d2", name="Sam's Pixel", online=False, last_seen=time.time() - 7200
            ),
        ]

    async def status(self) -> list[Row]:
        return [
            Row("TV browser", "bad", "Edge is not running.", "open_browser", "Open the TV browser")
        ]

    def devices(self) -> list[DevicePresence]:
        return [p for p in self.phones if p.device_id not in self.revoked]

    def revoke(self, device_id: str) -> None:
        self.revoked.append(device_id)

    def licence(self) -> Licence:
        return self.lic

    def uses_store(self) -> bool:
        return False

    def enter_key(self, text: str) -> bool:
        if text != "ARMREST1.good.key":
            return False
        self.lic = Licence("active", "key", holder="Jane")
        return True

    def buy(self) -> str:
        return "The Armrest website is open on the TV."

    async def open_browser(self) -> bool:
        return True

    async def reconnect(self) -> None:
        return None

    def brain_setup(self) -> str:
        return "The download runs in the new window."

    def pairing_url(self) -> str | None:
        return "https://armrest-app.pages.dev/?room=room-abcdef123&secret=abcdefghijklmnop"

    def restart(self) -> None:
        self.restarted += 1

    async def delete_cloud(self) -> str | None:
        return None

    def diagnostics(self) -> str:
        return "Armrest diagnostics\n"

    def open_url(self, url: str) -> None:
        self.opened.append(url)

    def notices_path(self) -> Path | None:
        return None


@pytest.fixture(scope="module")
def qapp():  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.gui
def test_settings_page_saves_the_form_and_restarts(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QCheckBox, QLineEdit

    from control.window import ControlWindow

    backend = FakeBackend(tmp_path)
    window = ControlWindow(backend)
    remote = window.inputs["CJ_REMOTE_INPUT"]
    assert isinstance(remote, QCheckBox) and remote.isChecked()
    remote.setChecked(False)
    lang = window.inputs["CJ_SUBTITLE_LANG"]
    assert isinstance(lang, QLineEdit)
    lang.setText("French")
    assert window.save_settings(restart=False) == []  # a bad value blocks the save
    assert window.errors["CJ_SUBTITLE_LANG"].text() and not backend.config_path.exists()
    lang.setText("fr")
    assert sorted(window.save_settings(restart=False)) == ["CJ_REMOTE_INPUT", "CJ_SUBTITLE_LANG"]
    values = settings_file.read_values(backend.config_path)
    assert values == {"CJ_REMOTE_INPUT": "false", "CJ_SUBTITLE_LANG": "fr"}
    assert window.save_settings(restart=False) == []  # nothing changed the second time


@pytest.mark.gui
def test_phones_page_lists_and_removes_phones(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QLabel

    from control.window import ControlWindow

    backend = FakeBackend(tmp_path)
    window = ControlWindow(backend)
    window.open_page("Phones")
    texts = [w.text() for w in window.findChildren(QLabel)]
    assert "Jane's iPhone" in texts and "Connected now" in texts and "Last seen 2 h ago" in texts
    window.remove_phone("d2", "Sam's Pixel", confirm=False)
    assert backend.revoked == ["d2"]
    window.hide()


@pytest.mark.gui
def test_licence_page_activates_a_key(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from control.window import ControlWindow

    backend = FakeBackend(tmp_path)
    window = ControlWindow(backend)
    window.show_licence()
    assert "5 days left" in window._licence_text.text() and not window.buy_button.isHidden()
    window.key_input.setText("nope")
    assert not window.activate_key()
    window.key_input.setText("ARMREST1.good.key")
    assert window.activate_key()
    assert "licensed to Jane" in window._licence_text.text() and window.buy_button.isHidden()


@pytest.mark.gui
async def test_status_page_draws_rows_and_routes_fixes(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QPushButton

    from control.window import ControlWindow

    backend = FakeBackend(tmp_path)
    window = ControlWindow(backend)
    window.show_status(await backend.status())
    labels = [b.text() for b in window.findChildren(QPushButton)]
    assert "Open the TV browser" in labels
    window.run_fix("buy")
    assert window.stack.currentIndex() == 3  # Licence
    window.hide()


@pytest.mark.gui
def test_help_page_saves_diagnostics(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from control.window import ControlWindow

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    backend = FakeBackend(tmp_path)
    window = ControlWindow(backend)
    path = window.save_diagnostics()
    assert path.parent == tmp_path and path.read_text(encoding="utf-8").startswith("Armrest")


@pytest.mark.gui
def test_the_store_build_hides_the_update_notice_setting(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from control.window import ControlWindow

    backend = FakeBackend(tmp_path)
    backend.channel = "store"
    assert "CJ_UPDATE_CHECK" not in ControlWindow(backend).inputs


@pytest.mark.gui
def test_the_wizard_saves_the_choices_and_marks_setup_done(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from control.wizard import WelcomeWizard

    backend = FakeBackend(tmp_path)
    state = UiState(tmp_path / "ui.json")
    wizard = WelcomeWizard(backend, on_finished=state.mark_setup_done)
    wizard.use_gemini.setChecked(True)
    wizard.gemini_key.setText("AIzaSyExample123")
    wizard.crash_reports.setChecked(True)
    assert not wizard.check_paired()
    backend.phones.append(
        DevicePresence(device_id="d3", name="Ana's phone", online=True, last_seen=time.time())
    )
    assert wizard.check_paired() and "Ana's phone" in wizard.pair_note.text()
    changed = wizard.finish_setup()
    assert sorted(changed) == ["CJ_CRASH_REPORTS", "CJ_GEMINI_API_KEY"]
    assert state.setup_done
    assert settings_file.read_values(backend.config_path)["CJ_CRASH_REPORTS"] == "true"


@pytest.mark.gui
def test_the_wizard_never_saves_a_malformed_key(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from control.wizard import WelcomeWizard

    backend = FakeBackend(tmp_path)
    wizard = WelcomeWizard(backend, on_finished=lambda: None)
    wizard.use_gemini.setChecked(True)
    wizard.gemini_key.setText("oops")
    assert "CJ_GEMINI_API_KEY" not in wizard.choices()
