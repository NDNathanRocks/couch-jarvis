from __future__ import annotations

import json
from pathlib import Path

import pytest

from telemetry import MAX_ROTATIONS, Telemetry, percentile


def read_records(directory: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return out


def test_span_nesting_and_jsonl_shape(telemetry: Telemetry, telemetry_dir: Path) -> None:
    with (
        telemetry.span("cmd.lifecycle", attributes={"cj.command_id": "c1"}) as root,
        telemetry.span("agent.node.dom_executor", attributes={"cj.tier": "dom"}) as child,
    ):
        child.set_attribute("cj.attempt", 1)
    records = {r["name"]: r for r in read_records(telemetry_dir)}
    child_rec = records["agent.node.dom_executor"]
    root_rec = records["cmd.lifecycle"]
    assert child_rec["trace_id"] == root_rec["trace_id"] == root.trace_id
    assert child_rec["parent_span_id"] == root.span_id
    assert root_rec["parent_span_id"] is None
    assert len(str(root_rec["trace_id"])) == 32 and len(str(root_rec["span_id"])) == 16
    assert child_rec["attributes"] == {"cj.tier": "dom", "cj.attempt": 1}
    assert root_rec["status"] == "OK"
    assert str(root_rec["ts"]).endswith("Z")
    assert float(str(root_rec["duration_ms"])) >= 0


def test_error_status_recorded_and_reraised(telemetry: Telemetry, telemetry_dir: Path) -> None:
    with pytest.raises(ValueError), telemetry.span("executor.step"):
        raise ValueError("bad")
    (rec,) = read_records(telemetry_dir)
    assert rec["status"] == "ERROR"
    assert rec["attributes"] == {"error.type": "ValueError"}  # type only, no message leakage


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) is None
    data = [float(n) for n in range(1, 101)]
    assert percentile(data, 50) == 50.0
    assert percentile(data, 95) == 95.0
    assert percentile([7.0], 95) == 7.0


def span_record(name: str, duration: float = 0.0, **attrs: object) -> dict[str, object]:
    return {"name": name, "duration_ms": duration, "attributes": attrs}


def test_rollup_math() -> None:
    t = Telemetry(None)
    for d in (100.0, 200.0, 300.0, 400.0):
        t.emit(span_record("cmd.lifecycle", d))
    for tier in ("dom", "dom", "dom", "vision"):
        t.emit(span_record("executor.step", **{"cj.tier": tier}))
    t.emit(span_record("agent.node.post_action_verifier", **{"cj.verify_passed": True}))
    t.emit(span_record("agent.node.post_action_verifier", **{"cj.verify_passed": False}))
    t.emit(
        span_record(
            "llm.call", **{"gen_ai.usage.input_tokens": 800, "gen_ai.usage.output_tokens": 200}
        )
    )
    r = t.rollup()
    assert r["commands"] == 4
    assert r["latency_ms"] == {"p50": 200.0, "p95": 400.0}
    assert r["tier_counts"] == {"dom": 3, "vision": 1}
    assert r["dom_rate"] == 0.75
    assert r["vision_fallback_rate"] == 0.25
    assert r["verify_pass_rate"] == 0.5
    assert r["tokens_total"] == 1000
    assert r["tokens_per_command"] == 250.0


def test_rollup_empty_is_safe() -> None:
    r = Telemetry(None).rollup()
    assert r["commands"] == 0 and r["dom_rate"] is None and r["tokens_per_command"] is None


def test_rotation_at_max_bytes(tmp_path: Path) -> None:
    t = Telemetry(tmp_path, max_bytes=150)
    for i in range(6):  # a few files, within the daily cap (test_telemetry_retention: beyond)
        t.emit(span_record("cmd.lifecycle", float(i)))
    files = list(tmp_path.glob("*.jsonl"))
    assert 2 <= len(files) <= MAX_ROTATIONS + 1
    assert len(read_records(tmp_path)) == 6  # nothing lost in rotation
    for p in files:
        assert p.stat().st_size <= 150  # rotation happens before a file would overflow


def test_ring_is_bounded() -> None:
    t = Telemetry(None, ring_size=3)
    for i in range(10):
        t.emit(span_record("x", float(i)))
    assert [r["duration_ms"] for r in t.recent()] == [7.0, 8.0, 9.0]
