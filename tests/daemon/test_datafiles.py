"""Data files: atomic writes and the version record that lets later releases migrate them."""

from __future__ import annotations

import json
from pathlib import Path

import datafiles


def test_atomic_write_replaces_the_file_whole(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "voice.json"
    datafiles.atomic_write_text(target, '{"a": 1}')
    datafiles.atomic_write_text(target, '{"a": 2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in target.parent.iterdir()] == ["voice.json"]  # no temp files left


def test_a_fresh_pc_has_no_data_version_yet(tmp_path: Path) -> None:
    assert datafiles.stored_version(tmp_path / "missing") == 0
    assert datafiles.migrate(tmp_path / "missing") == 1
    assert not (tmp_path / "missing").exists()  # nothing is created for a PC with no data


def test_0_3_0_data_is_version_1_and_is_left_as_it_is(tmp_path: Path) -> None:
    room = '{"room_id": "abc12345", "v": 1}'
    (tmp_path / "room.json").write_text(room, encoding="utf-8")
    assert datafiles.stored_version(tmp_path) == 1
    assert datafiles.migrate(tmp_path) == 1
    assert (tmp_path / "room.json").read_text(encoding="utf-8") == room
    assert json.loads((tmp_path / "schema.json").read_text(encoding="utf-8")) == {"version": 1}


def test_steps_run_in_order_once(tmp_path: Path) -> None:
    (tmp_path / "room.json").write_text("{}", encoding="utf-8")
    ran: list[int] = []
    steps = {1: lambda _f: ran.append(1), 2: lambda _f: ran.append(2)}
    assert datafiles.migrate(tmp_path, steps) == 3
    assert datafiles.migrate(tmp_path, steps) == 3
    assert ran == [1, 2]


def test_a_failed_step_keeps_the_last_good_version(tmp_path: Path) -> None:
    (tmp_path / "room.json").write_text("{}", encoding="utf-8")

    def boom(_folder: Path) -> None:
        raise RuntimeError("disk full")

    assert datafiles.migrate(tmp_path, {1: boom}) == 1
    assert datafiles.stored_version(tmp_path) == 1


def test_data_from_a_newer_app_is_never_downgraded(tmp_path: Path) -> None:
    (tmp_path / "schema.json").write_text('{"version": 9}', encoding="utf-8")
    assert datafiles.migrate(tmp_path) == 9
    assert json.loads((tmp_path / "schema.json").read_text(encoding="utf-8")) == {"version": 9}
