"""Structured JSONL telemetry with OpenTelemetry-compatible span fields.

One JSON object per finished span is appended to <dir>/YYYY-MM-DD.jsonl (rotated at
max_bytes). An in-memory ring of recent spans powers rollup() for GET /api/metrics.
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AttrValue = str | int | float | bool | None

_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "cj_current_span", default=None
)


def _hex(nbytes: int) -> str:
    return os.urandom(nbytes).hex()


class Span:
    def __init__(
        self,
        name: str,
        *,
        trace_id: str,
        parent_span_id: str | None,
        kind: str,
        attributes: dict[str, AttrValue],
        start_ns: int,
    ) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = _hex(8)
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.attributes = attributes
        self.start_ns = start_ns
        self.end_ns = start_ns
        self.status = "OK"

    def set_attribute(self, key: str, value: AttrValue) -> None:
        self.attributes[key] = value

    def set_error(self, exc: BaseException | str) -> None:
        self.status = "ERROR"
        self.attributes["error.type"] = exc if isinstance(exc, str) else type(exc).__name__


def new_trace_id() -> str:
    return _hex(16)


def current_span() -> Span | None:
    return _current_span.get()


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


class Telemetry:
    def __init__(
        self,
        directory: Path | None,
        *,
        max_bytes: int = 50 * 1024 * 1024,
        ring_size: int = 5000,
        time_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._dir = directory
        self._max_bytes = max_bytes
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._time_ns = time_ns
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ spans

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttrValue] | None = None,
        *,
        kind: str = "INTERNAL",
        trace_id: str | None = None,
    ) -> Iterator[Span]:
        parent = _current_span.get()
        sp = Span(
            name,
            trace_id=trace_id or (parent.trace_id if parent else _hex(16)),
            parent_span_id=parent.span_id if parent else None,
            kind=kind,
            attributes=dict(attributes or {}),
            start_ns=self._time_ns(),
        )
        token = _current_span.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.set_error(exc)
            raise
        finally:
            _current_span.reset(token)
            sp.end_ns = self._time_ns()
            self.emit(self._to_record(sp))

    @staticmethod
    def _to_record(sp: Span) -> dict[str, Any]:
        ts = datetime.fromtimestamp(sp.start_ns / 1e9, tz=UTC)
        return {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "trace_id": sp.trace_id,
            "span_id": sp.span_id,
            "parent_span_id": sp.parent_span_id,
            "name": sp.name,
            "kind": sp.kind,
            "start_ns": sp.start_ns,
            "end_ns": sp.end_ns,
            "duration_ms": round((sp.end_ns - sp.start_ns) / 1e6, 3),
            "status": sp.status,
            "attributes": sp.attributes,
        }

    # ------------------------------------------------------------------ sink

    def emit(self, record: dict[str, Any]) -> None:
        self._ring.append(record)
        if self._dir is None:
            return
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        try:
            path = self._target_path(len(line.encode("utf-8")))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            log.exception("telemetry write failed")

    def _target_path(self, incoming: int) -> Path:
        assert self._dir is not None
        day = datetime.fromtimestamp(self._time_ns() / 1e9, tz=UTC).strftime("%Y-%m-%d")
        path = self._dir / f"{day}.jsonl"
        if path.exists() and path.stat().st_size + incoming > self._max_bytes:
            n = 1
            while (self._dir / f"{day}.{n}.jsonl").exists():
                n += 1
            path.rename(self._dir / f"{day}.{n}.jsonl")
        return path

    def recent(self) -> list[dict[str, Any]]:
        return list(self._ring)

    # ------------------------------------------------------------------ rollup

    def rollup(self) -> dict[str, Any]:
        records = self.recent()
        lifecycle = [r for r in records if r["name"] == "cmd.lifecycle"]
        durations = [float(r["duration_ms"]) for r in lifecycle]

        steps = [
            r
            for r in records
            if r["name"] == "executor.step" and r["attributes"].get("cj.tier") is not None
        ]
        tier_counts: dict[str, int] = {}
        for r in steps:
            tier = str(r["attributes"]["cj.tier"])
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        total_steps = len(steps)

        verifies = [
            r["attributes"]["cj.verify_passed"]
            for r in records
            if r["attributes"].get("cj.verify_passed") is not None
        ]

        tokens = sum(
            int(r["attributes"].get("gen_ai.usage.input_tokens") or 0)
            + int(r["attributes"].get("gen_ai.usage.output_tokens") or 0)
            for r in records
        )
        commands = len(lifecycle)

        return {
            "commands": commands,
            "latency_ms": {
                "p50": percentile(durations, 50),
                "p95": percentile(durations, 95),
            },
            "tier_counts": tier_counts,
            "dom_rate": (tier_counts.get("dom", 0) / total_steps) if total_steps else None,
            "vision_fallback_rate": (
                tier_counts.get("vision", 0) / total_steps if total_steps else None
            ),
            "verify_pass_rate": (sum(1 for v in verifies if v) / len(verifies))
            if verifies
            else None,
            "tokens_total": tokens,
            "tokens_per_command": (tokens / commands) if commands else None,
        }
