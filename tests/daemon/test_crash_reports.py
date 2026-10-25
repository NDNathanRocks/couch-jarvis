"""Opt-in crash reports: off by default, and nothing that identifies the user leaves the PC."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import build_info
import crash_reports
from config import Settings

DSN = "https://0123abcd@o1.ingest.sentry.io/42"


def test_crash_reports_are_off_unless_the_user_turns_them_on() -> None:
    assert Settings(_env_file=None).crash_reports is False
    assert crash_reports.start(False, DSN) is False


def test_without_an_address_in_the_build_nothing_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crash_reports, "crash_dsn", lambda: None)
    assert crash_reports.start(True) is False


def test_the_address_is_read_from_the_build(tmp_path: Path) -> None:
    info = tmp_path / "build_info.json"
    assert build_info.read_crash_dsn(info) is None
    info.write_text(json.dumps({"channel": "store", "crash_dsn": DSN}), encoding="utf-8")
    assert build_info.read_crash_dsn(info) == DSN
    info.write_text(json.dumps({"crash_dsn": "http://not-https"}), encoding="utf-8")
    assert build_info.read_crash_dsn(info) is None


def test_turned_on_it_starts_sentry_with_the_private_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *_a: None)
    assert crash_reports.start(True, DSN) is True
    assert seen["dsn"] == DSN and seen["send_default_pii"] is False
    assert seen["server_name"] == "pc"  # never the computer's name
    assert seen["include_local_variables"] is False and seen["max_breadcrumbs"] == 0
    assert seen["traces_sample_rate"] == 0.0 and seen["before_send"] is crash_reports.scrub_event


def test_a_broken_reporter_never_stops_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    def boom(**_kw: Any) -> None:
        raise RuntimeError("no network")

    monkeypatch.setattr(sentry_sdk, "init", boom)
    assert crash_reports.start(True, DSN) is False


def test_text_is_scrubbed_of_anything_personal() -> None:
    text = crash_reports.scrub_text(
        r"failed on https://www.netflix.com/watch/123?t=9 for jane.doe@mail.com at "
        r"C:\Users\Jane\AppData\Roaming\CouchJarvis\room.json token eyJhbGciOiJIUzI1NiJ9abcdefghijk"
    )
    for leaked in ("netflix", "jane.doe", "Jane", "eyJhbGci"):
        assert leaked not in text
    assert r"C:\Users\<user>\AppData" in text


def test_an_event_keeps_only_what_helps_fix_the_bug() -> None:
    event = {
        "event_id": "e1",
        "level": "error",
        "server_name": "JANES-LAPTOP",
        "user": {"ip_address": "203.0.113.9", "username": "jane"},
        "request": {"url": "http://192.168.1.10:8765/api/command"},
        "breadcrumbs": {"values": [{"message": "play the weeknd"}]},
        "extra": {"command": "play the weeknd"},
        "contexts": {"os": {"name": "Windows"}, "device": {"name": "JANES-LAPTOP"}},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad reply from https://music.youtube.com/watch?v=abc",
                    "stacktrace": {
                        "frames": [
                            {
                                "abs_path": r"C:\Users\Jane\AppData\Local\Programs\CouchJarvis\x.py",
                                "function": "run",
                                "vars": {"room_secret": "s3cret"},
                                "pre_context": ["secret = 's3cret'"],
                            }
                        ]
                    },
                }
            ]
        },
    }
    clean = crash_reports.scrub_event(event)
    assert clean is not None
    dumped = json.dumps(clean)
    for leaked in (
        "JANES-LAPTOP",
        "203.0.113.9",
        "jane",
        "weeknd",
        "s3cret",
        "music.youtube",
        "192.168",
    ):
        assert leaked not in dumped, leaked
    frame = clean["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["function"] == "run" and "vars" not in frame
    assert clean["contexts"] == {"os": {"name": "Windows"}}
