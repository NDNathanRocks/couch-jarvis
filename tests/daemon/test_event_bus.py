from __future__ import annotations

import asyncio
import json

from contracts import Ping, dump_message
from event_bus import EventBus


def ping(i: int) -> Ping:
    return Ping(id=f"p{i}", ts=float(i), room_id="r", **{"from": "pc"})


async def test_publish_fans_out_and_numbers_events() -> None:
    bus = EventBus()
    async with bus.subscribe() as a, bus.subscribe() as b:
        bus.publish(ping(1))
        bus.publish(ping(2))
        got_a = [await a.get(), await a.get()]
        got_b = [await b.get(), await b.get()]
    assert [e.seq for e in got_a] == [1, 2] == [e.seq for e in got_b]
    assert got_a[0].type == "ping"
    assert json.loads(got_a[0].data)["id"] == "p1"
    assert bus.subscriber_count == 0


async def test_replay_since_last_event_id() -> None:
    bus = EventBus(ring_size=3)
    for i in range(5):
        bus.publish(ping(i))
    assert [e.seq for e in bus.replay_since(0)] == [3, 4, 5]  # ring only holds the newest 3
    assert [e.seq for e in bus.replay_since(4)] == [5]
    assert bus.replay_since(5) == []


async def test_slow_subscriber_drops_oldest_never_blocks() -> None:
    bus = EventBus(subscriber_queue_size=2)
    async with bus.subscribe() as q:
        for i in range(5):
            bus.publish(ping(i))
        assert [q.get_nowait().seq for _ in range(q.qsize())] == [4, 5]


def test_sse_frame_format() -> None:
    bus = EventBus()
    event = bus.publish(ping(1))
    frame = event.to_sse()
    assert frame == f"id: 1\nevent: ping\ndata: {dump_message(ping(1))}\n\n"
    assert frame.endswith("\n\n")


async def test_unsubscribe_on_cancel() -> None:
    bus = EventBus()

    async def consume() -> None:
        async with bus.subscribe() as q:
            await q.get()

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    assert bus.subscriber_count == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert bus.subscriber_count == 0
