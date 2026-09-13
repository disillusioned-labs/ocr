"""Logging contract: one JSON pipeline for app logs and library logs alike."""

from __future__ import annotations

import io
import json
import logging

import pytest

from ocr_engine.obs.logging import configure_logging, get_logger


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_app_and_library_logs_share_one_json_shape() -> None:
    stream = io.StringIO()
    configure_logging("info", "json", stream=stream)

    get_logger("test.app").info("hello", key="value")
    logging.getLogger("some.library").warning("foreign %s", "message")

    lines = _lines(stream)
    assert lines[0]["event"] == "hello"
    assert lines[0]["key"] == "value"
    assert lines[0]["level"] == "info"
    assert lines[0]["logger"] == "test.app"
    assert lines[1]["event"] == "foreign message"
    assert lines[1]["level"] == "warning"
    assert lines[1]["logger"] == "some.library"
    for line in lines:
        assert "timestamp" in line
        assert "trace_id" not in line  # no active span here


def test_text_format_and_level_filtering() -> None:
    stream = io.StringIO()
    configure_logging("warning", "text", stream=stream)

    get_logger("test.app").info("dropped")
    logging.getLogger("some.library").warning("kept")

    out = stream.getvalue()
    assert "dropped" not in out
    assert "kept" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip().splitlines()[-1])  # console-rendered, not JSON
