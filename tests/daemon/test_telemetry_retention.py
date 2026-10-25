"""Telemetry never grows without bound on a PC that runs for years (plan 5I)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from telemetry import MAX_ROTATIONS, Telemetry


def at(day: str) -> int:
    return (
        int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1e9) + 3600 * 10**9
    )


def test_days_older_than_two_weeks_are_deleted(tmp_path: Path) -> None:
    for name in (
        "2026-10-01.jsonl",
        "2026-10-01.1.jsonl",
        "2026-10-10.jsonl",
        "2026-10-24.jsonl",
        "notes.txt",
    ):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    tele = Telemetry(tmp_path, time_ns=lambda: at("2026-10-25"))
    tele.emit({"name": "x"})
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["2026-10-24.jsonl", "2026-10-25.jsonl", "notes.txt"]  # 10-10 is 15 days old


def test_a_runaway_day_keeps_a_bounded_number_of_files(tmp_path: Path) -> None:
    tele = Telemetry(tmp_path, max_bytes=200, time_ns=lambda: at("2026-10-25"))
    for i in range(200):
        tele.emit({"name": "flood", "i": i, "pad": "x" * 40})
    files = sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert len(files) == MAX_ROTATIONS + 1
    newest = (tmp_path / "2026-10-25.jsonl").read_text(encoding="utf-8")
    assert '"i":199' in newest  # the latest records are kept
