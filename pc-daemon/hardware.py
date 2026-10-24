"""What this PC can run, so defaults fit it: big models with a GPU, small ones without."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

GPU_SPEECH_MODEL, CPU_SPEECH_MODEL = "large-v3-turbo", "small"
GPU_TEXT_MODEL, CPU_TEXT_MODEL = "qwen2.5:7b", "qwen2.5:3b"


@lru_cache(maxsize=1)
def has_nvidia_gpu() -> bool:
    """An NVIDIA driver is installed (nvidia-smi ships with it)."""
    if shutil.which("nvidia-smi"):
        return True
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    return (system_root / "System32" / "nvidia-smi.exe").is_file()


def speech_model(setting: str, device: str) -> str:
    """`auto` means large-v3-turbo on a GPU and `small` on a CPU (large is far too slow there)."""
    if setting != "auto":
        return setting
    on_gpu = device == "cuda" or (device == "auto" and has_nvidia_gpu())
    return GPU_SPEECH_MODEL if on_gpu else CPU_SPEECH_MODEL


def text_model(setting: str) -> str:
    if setting != "auto":
        return setting
    return GPU_TEXT_MODEL if has_nvidia_gpu() else CPU_TEXT_MODEL
