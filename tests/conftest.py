from __future__ import annotations

from pathlib import Path

import pytest

from support import AgentEnv, make_env
from telemetry import Telemetry


@pytest.fixture
def telemetry_dir(tmp_path: Path) -> Path:
    return tmp_path / "telemetry"


@pytest.fixture
def telemetry(telemetry_dir: Path) -> Telemetry:
    return Telemetry(telemetry_dir)


@pytest.fixture
def env() -> AgentEnv:
    return make_env()
