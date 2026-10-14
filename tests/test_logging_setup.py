"""Tests for structured JSON logging."""

from __future__ import annotations

import json
import logging

from lolcoach.logging_setup import JsonFormatter


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="lolcoach.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_line_is_valid_json_with_required_keys() -> None:
    formatter = JsonFormatter("lol-coach")
    payload = json.loads(formatter.format(_record()))
    assert payload["level"] == "INFO"
    assert payload["service"] == "lol-coach"
    assert payload["logger"] == "lolcoach.test"
    assert payload["message"] == "hello world"
    assert payload["timestamp"].endswith("Z")


def test_data_field_is_included_only_when_present() -> None:
    formatter = JsonFormatter("lol-coach")
    assert "data" not in json.loads(formatter.format(_record()))
    with_data = json.loads(formatter.format(_record(data={"champion": "Galio"})))
    assert with_data["data"] == {"champion": "Galio"}
