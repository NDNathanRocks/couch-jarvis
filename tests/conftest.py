from __future__ import annotations

from pathlib import Path

import pytest

from support import AgentEnv, make_env
from telemetry import Telemetry


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the sync-Playwright e2e tests last.

    sync Playwright leaves an event loop running in the process, after which anything that calls
    asyncio.run() (the real-Edge integration fixtures) fails with "cannot be called from a
    running event loop". Each group passes alone; this keeps a full run clean.
    """
    items.sort(key=lambda item: "/e2e/" in item.nodeid.replace("\\", "/"))


@pytest.fixture
def telemetry_dir(tmp_path: Path) -> Path:
    return tmp_path / "telemetry"


@pytest.fixture
def telemetry(telemetry_dir: Path) -> Telemetry:
    return Telemetry(telemetry_dir)


@pytest.fixture
def env() -> AgentEnv:
    return make_env()
