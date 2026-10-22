"""Pydantic Settings for the daemon (.env loader) and Windows DPI-awareness init."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _local_app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "CouchJarvis"


def _roaming_app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "CouchJarvis"


def _default_edge_path() -> Path:
    for candidate in _EDGE_CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(_EDGE_CANDIDATES[0])


def resource_root() -> Path:
    """Where bundled resources live: the repo root, or PyInstaller's unpack directory."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parent.parent


def user_config_file() -> Path:
    """Per-user settings written by the installer / first run (edit this to change settings)."""
    return _roaming_app_dir() / "config.env"


class Settings(BaseSettings):
    # Later files win: a .env in the working directory (development) overrides the user config.
    model_config = SettingsConfigDict(
        env_prefix="CJ_",
        env_file=(user_config_file(), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Cloud relay / PWA
    relay_ws_url: str | None = None
    pwa_base_url: str = "http://localhost:5173"

    # LAN server
    lan_host: str = "0.0.0.0"
    lan_port: int = 8765
    lan_tls_cert: Path | None = None
    lan_tls_key: Path | None = None

    # Edge / CDP
    edge_path: Path = _default_edge_path()
    edge_profile_dir: Path = _local_app_dir() / "edge-profile"
    cdp_port: int = 9222

    # STT
    stt_model: str = "large-v3-turbo"
    stt_device: Literal["cuda", "cpu", "auto"] = "cuda"
    groq_api_key: SecretStr | None = None

    # Router / VLM
    router_provider: Literal["ollama", "gemini"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_text_model: str = "qwen2.5:7b"
    ollama_vlm_model: str = "qwen2.5vl:7b"
    gemini_api_key: SecretStr | None = None

    # Language "turn on subtitles" picks when you do not name one (ISO 639-1 code: en, es, fr...).
    subtitle_lang: str = "en"

    # Let paired phones drive the cursor/keyboard (touchpad). Pair only people you would trust
    # at your keyboard; set false to keep phones to media/browser commands.
    remote_input: bool = True

    # Storage / misc
    telemetry_dir: Path = _local_app_dir() / "telemetry"
    room_file: Path = _roaming_app_dir() / "room.json"
    hud_screen_index: int = 0
    log_level: str = "INFO"

    @property
    def lan_tls_enabled(self) -> bool:
        return self.lan_tls_cert is not None and self.lan_tls_key is not None

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def init_dpi_awareness() -> bool:
    """Make the process per-monitor-v2 DPI aware so screen coordinates are physical pixels.

    Returns True when the call succeeded; False on non-Windows or failure. Must run before any
    GUI, screenshot or mouse code.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        return bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except (AttributeError, OSError):
        try:
            return bool(ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0)
        except (AttributeError, OSError):
            return False
