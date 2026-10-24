"""The language model ("brain"): start Ollama if it is installed but not running, load the model.

On a sofa PC nobody opens a terminal, so the daemon makes sure the local brain is up before the
first command instead of failing with "offline" and leaving the cause a mystery.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

log = logging.getLogger(__name__)

State = Literal["ready", "cloud", "not_installed", "not_running", "model_missing"]


@dataclass(frozen=True)
class BrainStatus:
    state: State
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state in ("ready", "cloud")


def find_ollama() -> Path | None:
    """ollama.exe from the per-user installer, the all-users one, or PATH."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    programs = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    for candidate in (
        local / "Programs" / "Ollama" / "ollama.exe",
        programs / "Ollama" / "ollama.exe",
    ):
        if candidate.is_file():
            return candidate
    from shutil import which

    found = which("ollama")
    return Path(found) if found else None


def start_ollama(exe: Path) -> None:
    """`ollama serve` with no window, detached so it outlives this call."""
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(  # noqa: S603
        [str(exe), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


async def _models(client: httpx.AsyncClient, url: str) -> list[str] | None:
    """Names of the pulled models, or None when Ollama is not answering."""
    try:
        resp = await client.get(url.rstrip("/") + "/api/tags", timeout=3.0)
        resp.raise_for_status()
        return [str(m.get("name", "")) for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return None


def _has_model(names: list[str], wanted: str) -> bool:
    base = wanted if ":" in wanted else wanted + ":latest"
    return any(n in (base, wanted) for n in names)


async def prepare_ollama(
    url: str,
    model: str,
    *,
    client: httpx.AsyncClient | None = None,
    finder: Callable[[], Path | None] = find_ollama,
    starter: Callable[[Path], None] = start_ollama,
    wait_s: float = 25.0,
    poll_s: float = 1.0,
) -> BrainStatus:
    """Make the local brain ready: start the server if needed, then preload the model."""
    own = client is None
    http = client or httpx.AsyncClient()
    try:
        names = await _models(http, url)
        if names is None:
            exe = finder()
            if exe is None:
                return BrainStatus("not_installed", "Ollama is not installed")
            log.info("starting Ollama (%s)", exe)
            starter(exe)
            waited = 0.0
            while names is None and waited < wait_s:
                await asyncio.sleep(poll_s)
                waited += poll_s
                names = await _models(http, url)
            if names is None:
                return BrainStatus("not_running", "Ollama did not start")
        if not _has_model(names, model):
            return BrainStatus("model_missing", f"model {model} is not pulled")
        try:  # load it into memory now: the first request after boot is the slow one
            await http.post(
                url.rstrip("/") + "/api/generate",
                json={"model": model, "keep_alive": "30m", "prompt": ""},
                timeout=180.0,
            )
        except httpx.HTTPError:
            log.warning("could not preload %s", model, exc_info=True)
        return BrainStatus("ready", model)
    finally:
        if own:
            await http.aclose()
