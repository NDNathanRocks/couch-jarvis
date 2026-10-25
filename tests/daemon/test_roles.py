"""Owner and guest phones (plan 5H): the PC enforces what a guest may do."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent.policy import GUEST_REFUSAL, guest_refusal
from contracts import (
    DevicePresence,
    ErrorMsg,
    InputPointer,
    InputPointerPayload,
    envelope,
)
from event_bus import EventBus
from roles import RoleStore
from service import CommandService
from support import AgentEnv
from timers import TimerService


def test_roles_are_saved_and_default_to_owner(tmp_path: Path) -> None:
    store = RoleStore(tmp_path / "roles.json")
    assert store.role("d1") == "owner"
    assert store.set_role("d1", "guest") and not store.set_role("d1", "guest")
    assert RoleStore(tmp_path / "roles.json").role("d1") == "guest"
    store.forget("d1")
    assert RoleStore(tmp_path / "roles.json").role("d1") == "owner"


def test_a_broken_roles_file_means_everyone_is_an_owner(tmp_path: Path) -> None:
    (tmp_path / "roles.json").write_text("not json", encoding="utf-8")
    assert RoleStore(tmp_path / "roles.json").role("d1") == "owner"


def test_guests_may_not_change_the_pc_or_type() -> None:
    for op, args in [
        ("press_key", {"key": "f11"}),
        ("type", {"text": "hi"}),
        ("qr_control", {"action": "reset"}),
        ("browser_pref", {"adblock": False}),
        ("voice_pref", {"enabled": False}),
        ("bookmark", {"action": "remove", "name": "x"}),
    ]:
        assert guest_refusal([(op, args)]) == GUEST_REFUSAL, op
    for op, args in [
        ("media_key", {"key": "pause"}),
        ("navigate", {"url": "https://www.netflix.com/"}),
        ("bookmark", {"action": "open", "name": "x"}),
        ("seek", {"delta_s": 10}),
    ]:
        assert guest_refusal([(op, args)]) is None, op


def service_for(env: AgentEnv, tmp_path: Path) -> CommandService:
    service = CommandService(
        room_id="r1",
        bus=EventBus(),
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda info: asyncio.sleep(0)),
        mode="lan",
    )
    service.roles = RoleStore(tmp_path / "roles.json")
    env.deps.is_guest = lambda d: service.roles.role(d) == "guest"
    return service


async def test_a_guest_gets_no_touchpad_and_every_phone_sees_the_role(
    env: AgentEnv, tmp_path: Path
) -> None:
    service = service_for(env, tmp_path)
    service.set_cloud_devices(
        [DevicePresence(device_id="g1", name="Guest phone", online=True, last_seen=0)]
    )
    move = envelope(InputPointer, "r1", InputPointerPayload(dx=5, dy=0))
    assert await service.handle_envelope(move, "g1") == []  # an owner moves the mouse
    service.set_role("g1", "guest")
    assert service.state.state.devices[0].role == "guest"
    replies = await service.handle_envelope(move, "g1")
    assert isinstance(replies[0], ErrorMsg) and replies[0].payload.code == "guest"


async def test_the_agent_refuses_a_guests_settings_change(env: AgentEnv, tmp_path: Path) -> None:
    from command_queue import CommandRequest

    service = service_for(env, tmp_path)
    service.set_role("g1", "guest")
    runner = env.runner()
    refused = await runner.run(
        CommandRequest(
            command_id="c1",
            idempotency_key="k1",
            device_id="g1",
            kind="text",
            text="turn off pop-up protection",
        )
    )
    assert not refused.ok and refused.message == GUEST_REFUSAL
    allowed = await runner.run(
        CommandRequest(
            command_id="c2", idempotency_key="k2", device_id="g1", kind="text", text="pause"
        )
    )
    assert allowed.ok
