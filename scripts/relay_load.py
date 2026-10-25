"""Load test for a STAGING relay: many simulated TVs, each with a phone, sending commands.

    python scripts/relay_load.py wss://<staging-api>/staging --rooms 500 --minutes 5

Every simulated PC registers a throwaway room, a phone pairs with it, and the phone sends a
button press every few seconds which the PC answers like the real daemon (ack + result + a state
patch). It reports the share of commands that made the round trip, latency percentiles, and the
message count, then estimates the monthly relay cost for that many active TVs.

Never point this at production: it creates real rooms (they expire, see ROOM_TTL_S) and the
per-IP limits in connect_handler will refuse it after a few rooms unless the stage allows more.
Run it from several machines, or raise NEW_ROOMS_PER_HOUR on staging, to simulate many homes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import websockets

# Rough public prices (USD, us-east-2, after the free tier); check the AWS pricing pages.
PRICE_PER_MILLION_MESSAGES = 1.00
PRICE_PER_MILLION_CONNECTION_MINUTES = 0.25
PRICE_PER_MILLION_LAMBDA_CALLS = 0.20 + 0.40  # requests + ~20 ms at 256 MB arm64


def env(type_: str, room: str, payload: dict[str, Any], reply_to: str | None = None) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": type_,
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "room_id": room,
            "from": "load",
            "reply_to": reply_to,
            "payload": payload,
        }
    )


@dataclass
class Stats:
    sent: int = 0
    answered: int = 0
    messages: int = 0
    failures: int = 0
    latencies: list[float] = field(default_factory=list)


async def one_home(url: str, minutes: float, every_s: float, stats: Stats) -> None:
    room, secret, pc_key = (
        secrets.token_urlsafe(9),
        secrets.token_urlsafe(24),
        secrets.token_urlsafe(32),
    )
    headers = {
        "Authorization": f"Bearer {pc_key}",
        "X-Room-Secret-Hash": hashlib.sha256(secret.encode()).hexdigest(),
    }
    pc_url = f"{url}?{urlencode({'room': room, 'role': 'pc'})}"
    try:
        async with websockets.connect(pc_url, additional_headers=headers) as pc:
            await pc.send(env("state.sync", room, {}))
            async with websockets.connect(
                f"{url}?{urlencode({'room': room, 'pair': '1'})}"
            ) as phone:
                await phone.send(
                    env("pair.request", room, {"room_secret": secret, "device_name": "load"})
                )
                pending: dict[str, float] = {}

                async def pc_side() -> None:
                    async for raw in pc:
                        stats.messages += 1
                        msg = json.loads(raw)
                        if msg["type"] == "cmd.button":
                            cid = msg["payload"]["command_id"]
                            for kind, payload in (
                                ("cmd.ack", {"command_id": cid, "status": "accepted"}),
                                ("cmd.result", {"command_id": cid, "ok": True, "message": "ok"}),
                                ("state.patch", {"rev": 1}),
                            ):
                                await pc.send(env(kind, room, payload))
                                stats.messages += 1

                async def phone_side() -> None:
                    async for raw in phone:
                        stats.messages += 1
                        msg = json.loads(raw)
                        if msg["type"] == "cmd.result":
                            started = pending.pop(msg["payload"]["command_id"], None)
                            if started is not None:
                                stats.answered += 1
                                stats.latencies.append(time.monotonic() - started)

                tasks = [asyncio.create_task(pc_side()), asyncio.create_task(phone_side())]
                end = time.monotonic() + minutes * 60
                while time.monotonic() < end:
                    cid = str(uuid.uuid4())
                    pending[cid] = time.monotonic()
                    body = {"command_id": cid, "idempotency_key": cid, "action": "play_pause"}
                    await phone.send(env("cmd.button", room, body))
                    stats.sent += 1
                    stats.messages += 1
                    await asyncio.sleep(every_s)
                for t in tasks:
                    t.cancel()
    except Exception:  # noqa: BLE001 - count it, keep the other homes running
        stats.failures += 1


async def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("url")
    p.add_argument("--rooms", type=int, default=50)
    p.add_argument("--minutes", type=float, default=2.0)
    p.add_argument("--every", type=float, default=5.0, help="seconds between button presses")
    args = p.parse_args()
    if "/prod" in args.url:
        raise SystemExit("Refusing to load-test a /prod stage. Use the staging stack.")
    stats = Stats()
    started = time.monotonic()
    await asyncio.gather(
        *(one_home(args.url, args.minutes, args.every, stats) for _ in range(args.rooms))
    )
    elapsed_min = (time.monotonic() - started) / 60
    lat = sorted(stats.latencies)
    print(
        f"homes {args.rooms}, failed {stats.failures}, "
        f"commands {stats.sent}, answered {stats.answered}"
    )
    if lat:
        p95 = lat[int(len(lat) * 0.95) - 1]
        print(f"round trip p50 {statistics.median(lat) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms")
    per_home_month = stats.messages / max(args.rooms, 1) / max(elapsed_min, 1e-6) * 60 * 24 * 30
    print(f"messages per home per month at this pace: {per_home_month:,.0f}")
    cost = (
        per_home_month * PRICE_PER_MILLION_MESSAGES / 1e6
        + 2 * 43_200 * PRICE_PER_MILLION_CONNECTION_MINUTES / 1e6  # PC + phone online all month
        + per_home_month * PRICE_PER_MILLION_LAMBDA_CALLS / 1e6
    )
    print(f"estimated relay cost per home per month at this pace (no free tier): ${cost:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
