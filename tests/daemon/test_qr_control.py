"""Showing, hiding and resetting the pairing QR from the phone, the taskbar and a second launch."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn

from agent.fastpath import plan_text
from app_entry import toggle_running_qr
from command_queue import CommandRequest
from config import Settings
from executors.base import ActionResult
from local_api import create_app
from main import Runtime, build_runtime
from support import make_env


@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("hide the qr code", "hide"),
        ("remove the QR code", "hide"),
        ("turn off the qr", "hide"),
        ("get rid of the qr code on the tv", "hide"),
        ("toggle the qr code", "toggle"),
        ("reset the qr code", "reset"),
        ("refresh the qr code", "reset"),
        ("generate a new qr code", "reset"),
    ],
)
def test_spoken_qr_commands(said: str, action: str) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("qr_control", {"action": action})]


def test_show_still_shows() -> None:
    plan = plan_text("show the qr code")
    assert plan is not None and [s.op for s in plan.steps] == ["show_qr"]


async def test_the_agent_passes_the_action_to_the_tv() -> None:
    env = make_env()
    seen: list[str] = []

    async def qr_action(action: str) -> ActionResult:
        seen.append(action)
        return ActionResult(True, "os")

    env.deps.qr_action = qr_action
    req = CommandRequest(
        command_id="c", idempotency_key="k", device_id="d", kind="text", text="reset the qr code"
    )
    result = await env.runner().run(req)
    assert result.ok and result.message == "New QR code ready" and seen == ["reset"]


async def test_without_a_tv_overlay_it_says_so() -> None:
    env = make_env()
    req = CommandRequest(
        command_id="c", idempotency_key="k", device_id="d", kind="text", text="hide the qr code"
    )
    result = await env.runner(max_attempts=0).run(req)
    assert not result.ok


@dataclass
class Live:
    port: int
    rt: Runtime


@pytest.fixture
async def live() -> AsyncIterator[Live]:
    rt = build_runtime(Settings(_env_file=None), dry_run=True)
    app = create_app(rt.service, rt.telemetry)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    await rt.service.start()
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    yield Live(sock.getsockname()[1], rt)
    server.should_exit = True
    await task
    await rt.service.stop()


async def test_second_launch_toggles_the_running_app(live: Live) -> None:
    shown = [True]

    def toggle() -> bool:
        shown[0] = not shown[0]
        return shown[0]

    live.rt.service.on_qr_toggle = toggle
    result = await asyncio.to_thread(toggle_running_qr, live.port)
    assert result is False and shown == [False]
    assert await asyncio.to_thread(toggle_running_qr, live.port) is True


async def test_no_overlay_means_no_toggle(live: Live) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"http://127.0.0.1:{live.port}/api/qr/toggle")
    assert resp.status_code == 409
    assert await asyncio.to_thread(toggle_running_qr, live.port) is None


def test_nothing_listening_is_not_an_error() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert toggle_running_qr(free) is None
