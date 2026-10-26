from __future__ import annotations

import pytest

from config import Settings, init_dpi_awareness


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("CJ_CDP_PORT", "CJ_LAN_PORT", "CJ_STT_DEVICE"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.cdp_port == 9222
    assert s.lan_port == 8765
    assert s.stt_device == "auto"
    assert s.cdp_url == "http://127.0.0.1:9222"
    assert not s.lan_tls_enabled


def test_env_override_and_secret_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CJ_CDP_PORT", "9333")
    monkeypatch.setenv("CJ_GROQ_API_KEY", "gsk_secret")
    s = Settings(_env_file=None)
    assert s.cdp_port == 9333
    assert s.groq_api_key is not None
    assert "gsk_secret" not in repr(s)
    assert s.groq_api_key.get_secret_value() == "gsk_secret"


def test_tls_requires_both_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CJ_LAN_TLS_CERT", "cert.pem")
    assert not Settings(_env_file=None).lan_tls_enabled
    monkeypatch.setenv("CJ_LAN_TLS_KEY", "key.pem")
    assert Settings(_env_file=None).lan_tls_enabled


def test_invalid_stt_device_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CJ_STT_DEVICE", "tpu")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_dpi_init_returns_bool() -> None:
    assert isinstance(init_dpi_awareness(), bool)


def test_qr_address_moves_only_when_the_move_is_live() -> None:
    from brand import SITE_URL, qr_base_url

    old = "https://couch-jarvis-remote.vercel.app"
    assert qr_base_url(old, live=False) == old
    assert qr_base_url(old + "/", live=True) == SITE_URL
    assert qr_base_url("https://my.own.host", live=True) == "https://my.own.host"  # untouched
    assert qr_base_url("http://localhost:5173", live=True) == "http://localhost:5173"
