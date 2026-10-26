"""Licensing (plan.md 5F): Store trial and purchase, offline keys for the direct download, and
what the app does when a trial ends."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import licensing
from contracts import (
    CmdButton,
    CmdButtonPayload,
    CmdText,
    CmdTextPayload,
    Empty,
    ErrorMsg,
    StateSync,
    envelope,
)
from event_bus import EventBus
from licensing import DAY_S, Licence, LicenceFile, LicenceManager, is_preview
from service import CommandService
from support import AgentEnv
from timers import TimerService

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_800_000_000.0

PRIVATE = Ed25519PrivateKey.generate()
PUBLIC = licensing._b64e(
    PRIVATE.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
)


def key(name: str = "Jane") -> str:
    return licensing.issue_key(PRIVATE, name, "1042", dt.date(2026, 11, 1))


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def manager(
    tmp_path: Path,
    channel: str = "direct",
    *,
    packaged: bool = False,
    clock: Clock | None = None,
    store: Licence | Exception | None = None,
    opened: list[str] | None = None,
) -> LicenceManager:
    async def query() -> Licence:
        if isinstance(store, Exception):
            raise store
        assert store is not None
        return store

    return LicenceManager(
        channel,  # type: ignore[arg-type]
        packaged,
        LicenceFile(tmp_path / "license.json"),
        store_query=query,
        public_key_b64=PUBLIC,
        clock=clock or Clock(),
        open_url=(opened.append if opened is not None else lambda _u: None),
    )


# ----------------------------------------------------------------------------- keys


def test_a_genuine_key_is_accepted_even_when_wrapped_in_an_email() -> None:
    text = key()
    info = licensing.verify_key(text[:40] + "\n  " + text[40:], PUBLIC)
    assert info is not None and info.name == "Jane" and info.order == "1042"


def test_forged_or_mistyped_keys_are_refused() -> None:
    text = key()
    prefix, body, sig = text.split(".")
    other = Ed25519PrivateKey.generate()
    forged = licensing.issue_key(other, "Jane", "1042", dt.date(2026, 11, 1))
    tampered_body = licensing._b64e(
        json.dumps({"n": "Mallory", "o": "1", "i": "2026-01-01"}).encode()
    )
    for bad in (forged, f"{prefix}.{tampered_body}.{sig}", text[:-3] + "AAA", "hello", ""):
        assert licensing.verify_key(bad, PUBLIC) is None, bad


def test_the_app_ships_a_real_public_key_and_never_a_private_one() -> None:
    assert len(licensing._b64d(licensing.PUBLIC_KEY_B64)) == 32
    for path in ROOT.rglob("*"):
        if any(p in path.parts for p in ("node_modules", ".venv", ".git", "dist", "build")):
            continue
        if path.is_file() and path.suffix in {
            ".pem",
            ".key",
            ".py",
            ".ps1",
            ".json",
            ".txt",
            ".env",
        }:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "-----BEGIN " + "PRIVATE KEY-----" not in text, path


def test_the_key_tool_refuses_to_keep_the_private_key_in_the_repo(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "license_keys.py"
    inside = ROOT / "scripts" / "oops.pem"
    run = subprocess.run(
        [sys.executable, str(script), "new-keypair", "--out", str(inside)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1 and not inside.exists()


# ----------------------------------------------------------------------------- direct download


async def test_the_direct_download_starts_a_14_day_trial_then_expires(tmp_path: Path) -> None:
    clock = Clock()
    m = manager(tmp_path, clock=clock)
    assert (await m.refresh()) == Licence("trial", "trial", 14)
    clock.now = T0 + 13.5 * DAY_S
    assert (await m.refresh()).days_left == 1 and m.allows()
    clock.now = T0 + 14 * DAY_S
    assert (await m.refresh()).status == "expired" and not m.allows()
    assert "trial has ended" in m.refusal()


async def test_turning_the_clock_back_does_not_restart_the_trial(tmp_path: Path) -> None:
    clock = Clock()
    m = manager(tmp_path, clock=clock)
    await m.refresh()
    clock.now = T0 + 20 * DAY_S
    await m.refresh()
    clock.now = T0 + 1 * DAY_S  # rolled back
    assert (await m.refresh()).status == "expired"


async def test_a_key_unlocks_the_app_and_survives_a_restart(tmp_path: Path) -> None:
    clock = Clock()
    m = manager(tmp_path, clock=clock)
    await m.refresh()  # the trial starts
    clock.now = T0 + 30 * DAY_S
    await m.refresh()
    assert m.enter_key("not a key") is None and not m.allows()
    unlocked = m.enter_key(key("Jane"))
    assert unlocked == Licence("active", "key", holder="Jane") and m.allows()
    again = manager(tmp_path, clock=clock)
    assert (await again.refresh()).status == "active"
    assert again.state() is None  # nothing for the phones to show once paid


# ----------------------------------------------------------------------------- the Store


async def test_the_store_build_asks_the_store(tmp_path: Path) -> None:
    m = manager(tmp_path, "store", packaged=True, store=Licence("trial", "store", 5))
    assert (await m.refresh()) == Licence("trial", "store", 5)
    assert m.state() == {"status": "trial", "days_left": 5}


async def test_a_store_glitch_never_locks_out_a_buyer(tmp_path: Path) -> None:
    first = manager(tmp_path, "store", packaged=True, store=OSError("store service down"))
    assert (await first.refresh()).status == "active"  # never checked yet: benefit of the doubt
    paid = manager(tmp_path, "store", packaged=True, store=Licence("active", "store"))
    await paid.refresh()
    offline = manager(tmp_path, "store", packaged=True, store=OSError("offline"))
    assert offline.current.status == "active" and (await offline.refresh()).status == "active"


async def test_buy_opens_the_store_page_and_then_checks_often(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(licensing, "STORE_PRODUCT_ID", "9NTEST")
    opened: list[str] = []
    clock = Clock()
    m = manager(
        tmp_path,
        "store",
        packaged=True,
        store=Licence("expired", "store", 0),
        clock=clock,
        opened=opened,
    )
    await m.refresh()
    assert m.next_check_s() == m.TRIAL_S
    assert "Microsoft Store" in m.buy() and opened == ["ms-windows-store://pdp/?ProductId=9NTEST"]
    assert m.next_check_s() == m.FAST_S
    clock.now += m.FAST_FOR_S + 1
    assert m.next_check_s() == m.TRIAL_S


async def test_buy_opens_the_website_for_the_direct_download(tmp_path: Path) -> None:
    opened: list[str] = []
    m = manager(tmp_path, opened=opened)
    assert "website" in m.buy() and opened == [licensing.BUY_URL]


async def test_a_source_build_is_always_licensed(tmp_path: Path) -> None:
    m = manager(tmp_path, "dev", clock=Clock(T0 + 365 * DAY_S))
    assert (await m.refresh()) == licensing.LICENSED_DEV and m.state() is None


# ----------------------------------------------------------------------------- the service gate


class Gate:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.bought = 0

    def allows(self) -> bool:
        return self.allowed

    def refusal(self) -> str:
        return "Your free Armrest trial has ended. Tap Buy to keep using it."

    def buy(self) -> str:
        self.bought += 1
        return "The Microsoft Store page is open on the TV."


async def test_an_expired_trial_refuses_commands_but_buy_still_works(env: AgentEnv) -> None:
    bus = EventBus()
    service = CommandService(
        room_id="r1",
        bus=bus,
        runner=env.runner(),
        browser=env.browser,
        os_ctl=env.os,
        telemetry=env.telemetry,
        timers=TimerService(lambda info: asyncio.sleep(0)),
        mode="lan",
    )
    gate = Gate(allowed=False)
    service.licence = gate
    text = envelope(
        CmdText, "r1", CmdTextPayload(command_id="c1", idempotency_key="k1", text="play jazz")
    )
    replies = await service.handle_envelope(text, "phone-1")
    assert isinstance(replies[0], ErrorMsg) and replies[0].payload.code == "trial_ended"
    buy = envelope(
        CmdButton, "r1", CmdButtonPayload(command_id="c2", idempotency_key="k2", action="buy")
    )
    assert await service.handle_envelope(buy, "phone-1") == []
    assert gate.bought == 1
    results = [json.loads(e.data) for e in bus.replay_since(0) if e.type == "cmd.result"]
    assert results[-1]["payload"]["ok"] and "Store" in results[-1]["payload"]["message"]
    sync = await service.handle_envelope(envelope(StateSync, "r1", Empty()), "phone-1")
    assert sync[0].type == "state.snapshot"  # phones still see the TV
    gate.allowed = True
    assert await service.handle_envelope(text, "phone-1") == []  # bought: accepted again


# ----------------------------------------------------------------------------- the 0.x preview


def test_versions_before_1_0_are_a_free_preview() -> None:
    assert is_preview("0.4.0") and is_preview("0.10.2")
    assert not is_preview("1.0.0") and not is_preview("2.3.1") and not is_preview("junk")


async def test_a_preview_build_is_licensed_and_starts_no_trial_clock(tmp_path: Path) -> None:
    clock = Clock()
    preview = LicenceManager(
        "direct", False, LicenceFile(tmp_path / "license.json"), clock=clock, preview=True
    )
    assert preview.current.status == "active" and preview.current.source == "preview"
    clock.now += 60 * DAY_S
    assert (await preview.refresh()).status == "active"
    assert preview.state() is None  # phones show no trial banner
    assert not (tmp_path / "license.json").exists()  # the 1.0 trial has not started

    released = manager(tmp_path, clock=clock)  # the same PC, after updating to 1.0
    assert (await released.refresh()).status == "trial"
    assert released.current.days_left == 14


async def test_the_store_build_ignores_the_preview_flag(tmp_path: Path) -> None:
    store = LicenceManager(
        "store",
        True,
        LicenceFile(tmp_path / "license.json"),
        store_query=_store_trial,
        preview=True,
    )
    assert (await store.refresh()).source == "store"


async def _store_trial() -> Licence:
    return Licence("trial", "store", 5)
