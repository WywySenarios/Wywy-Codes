"""Tests for structured JSON-lines logging."""

from __future__ import annotations

import json
import os
import re

import pytest

from apps.orchestrator.state.logging import (
    LOG_BASE_DIR,
    VALID_SOURCES,
    LogWriter,
    _build_entry,
    tail_log,
)


class TestBuildEntry:
    def test_basic_entry_format(self):
        entry_str = _build_entry("INFO", "pipe-1", "GREEN", "orchestrator", "starting")
        entry = json.loads(entry_str)
        assert entry["level"] == "INFO"
        assert entry["pipeline"] == "pipe-1"
        assert entry["stage"] == "GREEN"
        assert entry["src"] == "orchestrator"
        assert entry["msg"] == "starting"
        assert "ctx" not in entry

    def test_entry_with_context(self):
        entry_str = _build_entry(
            "WARN", "pipe-1", "RED", "orchestrator",
            "retry", {"attempt": 3, "max": 5}
        )
        entry = json.loads(entry_str)
        assert entry["level"] == "WARN"
        assert entry["ctx"] == {"attempt": 3, "max": 5}

    def test_timestamp_format(self):
        """Timestamp must be ISO 8601 with milliseconds and Z suffix."""
        entry_str = _build_entry("INFO", "p", "s", "src", "msg")
        entry = json.loads(entry_str)
        ts_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
        assert re.match(ts_pattern, entry["ts"])

    def test_entry_ends_with_newline(self):
        entry_str = _build_entry("INFO", "p", "s", "src", "msg")
        assert entry_str.endswith("\n")
        assert not entry_str.endswith("\n\n")


class TestLogWriter:
    @pytest.fixture
    def log_path(self, tmp_path):
        return tmp_path / "test.log"

    def test_creates_parent_directory(self, tmp_path):
        deep_path = tmp_path / "sub" / "deep" / "log.log"
        writer = LogWriter(str(deep_path))
        assert os.path.isdir(os.path.dirname(str(deep_path)))

    def test_info_writes_entry(self, log_path):
        writer = LogWriter(str(log_path))
        writer.info("p1", "RED", "orchestrator", "start")
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["level"] == "INFO"

    def test_warn_writes_entry(self, log_path):
        writer = LogWriter(str(log_path))
        writer.warn("p1", "RED", "orchestrator", "warning", {"code": 1})
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["level"] == "WARN"
        assert entry["ctx"] == {"code": 1}

    def test_error_writes_entry(self, log_path):
        writer = LogWriter(str(log_path))
        writer.error("p1", "GREEN", "orchestrator", "crash", {"exit": 1})
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["level"] == "ERROR"

    def test_append_multiple_entries(self, log_path):
        writer = LogWriter(str(log_path))
        writer.info("p1", "s1", "src1", "one")
        writer.info("p1", "s1", "src1", "two")
        writer.info("p1", "s1", "src1", "three")
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_all_entries_are_valid_json(self, log_path):
        writer = LogWriter(str(log_path))
        writer.info("p1", "s1", "orchestrator", "info")
        writer.warn("p1", "s1", "orchestrator", "warn")
        writer.error("p1", "s1", "orchestrator", "error")
        with open(log_path) as f:
            for line in f:
                json.loads(line)

    def test_filepath_property(self, log_path):
        writer = LogWriter(str(log_path))
        assert str(writer.filepath) == str(log_path)


class TestTailLog:
    @pytest.fixture
    def log_base(self, tmp_path):
        return str(tmp_path)

    def test_returns_empty_for_missing_file(self, log_base):
        entries = tail_log("nonexistent", "stage", base_dir=log_base)
        assert entries == []

    def test_returns_last_n_entries(self, log_base):
        pipeline_dir = os.path.join(log_base, "p1")
        os.makedirs(pipeline_dir)
        log_path = os.path.join(pipeline_dir, "test.log")
        writer = LogWriter(log_path)
        for i in range(10):
            writer.info("p1", "s1", "src", f"msg {i}")

        entries = tail_log("p1", "test", base_dir=log_base)
        assert len(entries) == 10
        for i, entry in enumerate(entries):
            assert entry["msg"] == f"msg {i}"

    def test_handles_invalid_json_lines(self, log_base):
        """Lines that aren't valid JSON are captured with parse_error flag."""
        pipeline_dir = os.path.join(log_base, "p1")
        os.makedirs(pipeline_dir)
        log_path = os.path.join(pipeline_dir, "test.log")
        writer = LogWriter(log_path)
        writer.info("p1", "s1", "orchestrator", "good")
        with open(log_path, "a") as f:
            f.write("this is not json\n")
        writer.info("p1", "s1", "orchestrator", "also good")

        entries = tail_log("p1", "test", base_dir=log_base)
        assert len(entries) == 3
        assert entries[1].get("parse_error")


class TestValidSources:
    def test_all_stages_have_valid_source(self):
        expected_sources = frozenset({
            "orchestrator",
            "RED",
            "GREEN",
            "REFRACTOR",
            "compilance",
            "PR writer",
            "testing",
        })
        assert VALID_SOURCES == expected_sources

    def test_orchestrator_source_present(self):
        assert "orchestrator" in VALID_SOURCES


class TestLogBaseDir:
    def test_log_base_dir_is_absolute(self):
        assert LOG_BASE_DIR.startswith("/")
