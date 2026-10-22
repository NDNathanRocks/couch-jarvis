"""The packaged app (CouchJarvis.exe): config bootstrap, restart policy, frozen paths."""

from __future__ import annotations

from pathlib import Path

import pytest

import app_entry
from config import Settings, resource_root


def test_first_run_writes_the_default_config_once(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.env"
    defaults.write_text("CJ_RELAY_WS_URL=wss://relay.example/prod\n", encoding="utf-8")
    target = tmp_path / "appdata" / "config.env"

    assert app_entry.ensure_user_config(target, defaults) is True  # created
    assert target.read_text(encoding="utf-8") == "CJ_RELAY_WS_URL=wss://relay.example/prod\n"

    target.write_text("CJ_RELAY_WS_URL=wss://mine.example/prod\n", encoding="utf-8")
    assert app_entry.ensure_user_config(target, defaults) is False  # never overwrites edits
    assert "mine.example" in target.read_text(encoding="utf-8")


def test_missing_defaults_still_creates_a_commented_template(tmp_path: Path) -> None:
    target = tmp_path / "config.env"
    assert app_entry.ensure_user_config(target, tmp_path / "nope.env") is True
    assert target.read_text(encoding="utf-8").startswith("#")


def test_settings_read_the_user_config_and_a_local_env_overrides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = tmp_path / "config.env"
    user.write_text("CJ_PWA_BASE_URL=https://hosted.example\nCJ_STT_DEVICE=cpu\n", encoding="utf-8")
    local = tmp_path / ".env"
    local.write_text("CJ_STT_DEVICE=cuda\n", encoding="utf-8")
    for key in ("CJ_PWA_BASE_URL", "CJ_STT_DEVICE"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=(user, local))  # type: ignore[call-arg]
    assert s.pwa_base_url == "https://hosted.example"
    assert s.stt_device == "cuda"


def test_restart_delay_doubles_then_caps_and_resets_after_a_healthy_run() -> None:
    delay = app_entry.next_delay
    assert [delay(2, 5), delay(4, 5), delay(8, 5)] == [4, 8, 16]
    assert delay(60, 5) == 60  # capped
    assert delay(60, 120) == 2  # it ran for two minutes: start the ladder over


def test_worker_args_always_include_the_overlay() -> None:
    assert app_entry.worker_args([]) == ["--hud"]
    assert app_entry.worker_args(["--port", "9000"]) == ["--hud", "--port", "9000"]
    assert app_entry.worker_args(["--dry-run", "--hud"]) == ["--dry-run", "--hud"]
    assert app_entry.worker_args(["--no-hud", "--dry-run"]) == ["--dry-run"]  # headless


def test_one_shot_flags_apply_to_the_first_launch_only() -> None:
    """A crash-restart must not rotate the QR secret again (that would unpair every phone)."""
    first = ["--rotate-secret", "--port", "9000"]
    assert app_entry.without_one_shot(first) == ["--port", "9000"]
    assert app_entry.without_one_shot(["--hud"]) == ["--hud"]


def test_resource_root_follows_pyinstaller(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert (resource_root() / "pc-daemon").is_dir()  # running from the repo
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert resource_root() == tmp_path
