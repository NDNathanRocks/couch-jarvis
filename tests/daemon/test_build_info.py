"""Build channels (dev / direct / store) and the licensing guards for a product that is sold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_info
import hardware

ROOT = Path(__file__).resolve().parents[2]

# Models whose licence does not allow a commercial product. Never make one a default.
NON_COMMERCIAL_MODELS = {"qwen2.5:3b", "qwen2.5:72b"}


def test_channel_is_read_from_the_bundled_file(tmp_path: Path) -> None:
    info = tmp_path / "build_info.json"
    assert build_info.read_channel(info) == "dev"  # a source checkout has no file
    for name in ("direct", "store"):
        info.write_text(json.dumps({"channel": name}), encoding="utf-8")
        assert build_info.read_channel(info) == name
    info.write_text('{"channel": "nightly"}', encoding="utf-8")
    assert build_info.read_channel(info) == "dev"
    info.write_text("not json", encoding="utf-8")
    assert build_info.read_channel(info) == "dev"


@pytest.mark.parametrize(
    ("channel", "update_notice", "brain_installer", "hobby"),
    [("dev", True, True, True), ("direct", True, True, False), ("store", False, False, False)],
)
def test_what_each_channel_may_do(
    channel: build_info.Channel, update_notice: bool, brain_installer: bool, hobby: bool
) -> None:
    assert build_info.shows_update_notice(channel) is update_notice
    assert build_info.runs_brain_installer(channel) is brain_installer
    assert build_info.allows_hobby_services(channel) is hobby


def test_default_models_allow_commercial_use() -> None:
    assert hardware.GPU_TEXT_MODEL not in NON_COMMERCIAL_MODELS
    assert hardware.CPU_TEXT_MODEL not in NON_COMMERCIAL_MODELS
    script = (ROOT / "scripts" / "setup_brain.ps1").read_text(encoding="utf-8")
    for model in NON_COMMERCIAL_MODELS:
        assert f"'{model}'" not in script


def test_builds_never_ship_edge_tts() -> None:
    spec = (ROOT / "installer" / "couch_jarvis.spec").read_text(encoding="utf-8")
    hidden = spec[spec.index("hiddenimports +=") : spec.index("web_dist")]
    assert "edge_tts" not in hidden
    assert '"edge_tts",' in spec[spec.index("excludes = [") :]


def test_the_tv_browser_profile_inside_a_package(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    default = local / "CouchJarvis" / "edge-profile"
    own = local / "Packages" / "Fam_123" / "LocalCache" / "Local" / "CouchJarvis" / "edge-profile"
    assert build_info.edge_profile_dir(default, None, local) == default  # not packaged
    assert build_info.edge_profile_dir(default, "Fam_123", local) == own  # a new Store install
    default.mkdir(parents=True)  # the direct installer was here first: keep its sign-ins
    assert build_info.edge_profile_dir(default, "Fam_123", local) == default
    own.mkdir(parents=True)  # the package already has its own profile
    assert build_info.edge_profile_dir(default, "Fam_123", local) == own


def test_the_store_package_needs_a_store_build() -> None:
    script = (ROOT / "installer" / "build_msix.ps1").read_text(encoding="utf-8")
    assert "$builtChannel -ne 'store'" in script
