"""Quit really quits, restarts are immediate, and "delete my cloud data" starts the PC over."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import app_entry
import lifecycle
from config import Settings
from local_api import create_app
from main import Runtime, build_runtime, delete_cloud_data
from room import RoomStore


@pytest.fixture(autouse=True)
def clean_lifecycle() -> Iterator[None]:
    lifecycle.reset()
    yield
    lifecycle.reset()


def test_the_supervisor_stops_on_quit_restarts_now_on_request_and_backs_off_on_a_crash() -> None:
    assert app_entry.after_exit(lifecycle.EXIT_QUIT, 4.0, 100.0) is None
    assert app_entry.after_exit(lifecycle.EXIT_RESTART, 4.0, 100.0) == 0.0
    assert app_entry.after_exit(1, 4.0, 100.0) == 4.0  # a crash keeps the backoff
    assert app_entry.after_exit(0, 4.0, 100.0) == 4.0  # an unexplained exit is a crash too
    # asking to restart right after starting is treated like a crash: never a busy loop
    assert app_entry.after_exit(lifecycle.EXIT_RESTART, 4.0, 1.0) == 4.0


def test_request_exit_records_the_code_and_stops_the_daemon() -> None:
    stopped: list[bool] = []
    lifecycle.set_stopper(lambda: stopped.append(True))
    lifecycle.request_exit(lifecycle.EXIT_QUIT)
    assert stopped == [True] and lifecycle.exit_code() == lifecycle.EXIT_QUIT


def test_starting_over_makes_a_brand_new_room(tmp_path: Path) -> None:
    store = RoomStore(tmp_path / "room.json")
    old = store.load_or_create()
    store.set_room_key("k")
    new = store.start_over()
    assert (new.room_id, new.room_secret, new.pc_key) != (old.room_id, old.room_secret, old.pc_key)
    assert new.room_key is None
    assert RoomStore(tmp_path / "room.json").load_or_create() == new


class FakeCloud:
    def __init__(self, *, status: str = "up", confirms: bool = True) -> None:
        self.status = status
        self.confirms = confirms
        self.asked = 0

    async def delete_room(self) -> bool:
        self.asked += 1
        return self.confirms


def runtime(tmp_path: Path, cloud: Any) -> Runtime:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    rt.room_store = RoomStore(tmp_path / "room.json")
    rt.room_store.load_or_create()
    rt.cloud = cloud
    return rt


async def test_deleting_cloud_data_starts_over_and_restarts(tmp_path: Path) -> None:
    cloud = FakeCloud()
    rt = runtime(tmp_path, cloud)
    assert rt.room_store is not None
    before = rt.room_store.identity.room_id
    restarted: list[bool] = []
    lifecycle.set_stopper(lambda: restarted.append(True))
    assert await delete_cloud_data(rt) is None
    assert cloud.asked == 1 and rt.room_store.identity.room_id != before
    await asyncio.sleep(0.7)  # the answer goes out first, then the restart
    assert restarted == [True] and lifecycle.exit_code() == lifecycle.EXIT_RESTART


@pytest.mark.parametrize("cloud", [FakeCloud(status="down"), FakeCloud(confirms=False)])
async def test_nothing_local_changes_unless_the_cloud_confirmed(
    tmp_path: Path, cloud: FakeCloud
) -> None:
    rt = runtime(tmp_path, cloud)
    assert rt.room_store is not None
    before = rt.room_store.identity
    problem = await delete_cloud_data(rt)
    assert problem and rt.room_store.identity == before
    assert lifecycle.exit_code() == 0


async def test_the_endpoint_is_for_the_pc_owner_only(tmp_path: Path) -> None:
    rt = runtime(tmp_path, FakeCloud(confirms=False))
    app = create_app(rt.service, rt.telemetry, delete_cloud=lambda: delete_cloud_data(rt))
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=local, base_url="http://127.0.0.1") as c:
        resp = await c.post("/api/cloud/delete")
        assert resp.status_code == 502 and "did not confirm" in resp.json()["detail"]
    remote = httpx.ASGITransport(app=app, client=("192.168.1.20", 5000))
    async with httpx.AsyncClient(transport=remote, base_url="http://192.168.1.10:8765") as c:
        assert (await c.post("/api/cloud/delete")).status_code == 403
