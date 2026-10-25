"""The licence gate (scripts/licenses.py) that every installer build runs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("licenses", ROOT / "scripts" / "licenses.py")
assert _spec and _spec.loader
licenses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(licenses)


@pytest.mark.parametrize(
    "licence",
    ["MIT", "BSD-3-Clause", "Apache-2.0 OR MIT", "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"],
)
def test_licences_a_paid_app_may_ship(licence: str) -> None:
    assert licenses.allowed(licence)


@pytest.mark.parametrize(
    "licence",
    [
        "GNU General Public License v3 or later (GPLv3+)",
        "AGPL-3.0",
        "CC-BY-NC-4.0",
        "Qwen Research License",
        "UNKNOWN",
    ],
)
def test_licences_that_block_shipping(licence: str) -> None:
    assert not licenses.allowed(licence)


def test_gpl_helpers_are_excluded_from_builds() -> None:
    spec = (ROOT / "installer" / "couch_jarvis.spec").read_text(encoding="utf-8")
    excludes = spec[spec.index("excludes = [") :]
    for module in ("pymsgbox", "mouseinfo", "edge_tts"):
        assert f'"{module}"' in excludes
    assert {"pymsgbox", "mouseinfo", "edge-tts"} <= licenses.NOT_SHIPPED


def test_the_shipped_python_packages_pass() -> None:
    assert licenses.problems(licenses.python_packages()) == []
