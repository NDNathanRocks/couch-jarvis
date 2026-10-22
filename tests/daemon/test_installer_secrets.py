"""The installer must never carry the builder's API keys: users add their own, optionally."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
SPEC = (ROOT / "installer" / "couch_jarvis.spec").read_text(encoding="utf-8")
ISS = (ROOT / "installer" / "CouchJarvis.iss").read_text(encoding="utf-8")

# Settings the shipped defaults may set. Anything with KEY/SECRET/TOKEN in its name may not.
PUBLIC_SETTINGS = {"CJ_RELAY_WS_URL", "CJ_PWA_BASE_URL", "CJ_STT_DEVICE"}


def shipped_defaults() -> list[str]:
    block = re.search(r"\$defaults = @\((.*?)\n\)", BUILD, re.S)
    assert block, "build.ps1 no longer builds a $defaults block"
    return [line.strip().strip("'\"") for line in block.group(1).splitlines() if line.strip()]


def test_only_public_settings_are_active_in_the_shipped_defaults() -> None:
    active = [ln for ln in shipped_defaults() if ln and not ln.startswith("#")]
    names = {ln.split("=", 1)[0] for ln in active}
    assert names <= PUBLIC_SETTINGS, names - PUBLIC_SETTINGS
    assert not [n for n in names if re.search(r"KEY|SECRET|TOKEN|PASSWORD", n)]


def test_key_lines_in_the_defaults_are_commented_out_placeholders() -> None:
    keys = [ln for ln in shipped_defaults() if "API_KEY" in ln]
    assert keys, "users should be shown where to add their own keys"
    assert all(ln.startswith("#") and "..." in ln for ln in keys)  # '# CJ_GROQ_API_KEY=gsk_...'


def test_the_build_never_bundles_an_env_file() -> None:
    assert ".env" not in re.sub(r"defaults\.env", "", SPEC)  # only installer/defaults.env
    assert "config.env" not in SPEC
    data_lines = [ln for ln in SPEC.splitlines() if "datas.append" in ln]
    assert len(data_lines) == 2  # the phone app build and the (public) defaults, nothing else


def test_the_build_script_scans_the_output_for_the_builders_own_keys() -> None:
    assert (
        "Assert-NoSecrets" in BUILD and "CJ_GROQ_API_KEY" in BUILD and "CJ_GEMINI_API_KEY" in BUILD
    )
    assert BUILD.index("Assert-NoSecrets $app") < BUILD.index(
        "CreateFromDirectory"
    )  # before zipping


def test_the_installer_can_open_the_settings_file_for_the_user() -> None:
    assert "--open-settings" in ISS  # a Start-menu entry: where users paste their own keys
