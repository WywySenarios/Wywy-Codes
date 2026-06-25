"""Tests for structured JSON-lines logging.

Tests the new Django logging integration: ``PipelineFileHandler`` and
``_write_orchestrator_log_entry``, replacing the old ``LogWriter``,
``_build_entry``, and ``tail_log`` tests.
"""

from __future__ import annotations

import json
import logging
import re

import pytest

from apps.orchestrator.state.logging import (
    LOG_BASE_DIR,
    VALID_SOURCES,
    PipelineFileHandler,
    _write_orchestrator_log_entry,
)


# ═══════════════════════════════════════════════════════════════════════
#  Helper — capture handler for test assertions
# ═══════════════════════════════════════════════════════════════════════


class _RecordCaptureHandler(logging.Handler):
    """Capture :class:`logging.LogRecord` objects emitted by a logger.

    Used to verify the arguments passed to ``logger.log()`` by
    ``_write_orchestrator_log_entry`` without writing to the filesystem.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ═══════════════════════════════════════════════════════════════════════
#  PipelineFileHandler — unit tests
# ═══════════════════════════════════════════════════════════════════════


class TestPipelineFileHandler:
    """Direct tests for :meth:`PipelineFileHandler.emit`.

    These tests create ``LogRecord`` objects, call ``emit()`` on the
    handler, and assert on the resulting log file contents.  The handler
    reads ``settings.LOG_ROOT`` internally, so each test overrides it via
    the ``settings`` fixture to point at a temporary directory.
    """

    # ── Fixture helpers ──────────────────────────────────────────────

    @staticmethod
    def _make_record(
        msg: str,
        level: int = logging.INFO,
        *,
        pipeline_id: str | None = "pipe-1",
        stage: str | None = None,
        src: str | None = None,
        ctx: dict | None = None,
    ) -> logging.LogRecord:
        record = logging.LogRecord(
            "orchestrator.pipeline",
            level,
            pathname=__file__,
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        if pipeline_id is not None:
            record.pipeline_id = pipeline_id
        if stage is not None:
            record.stage = stage
        if src is not None:
            record.src = src
        if ctx is not None:
            record.ctx = ctx
        return record

    # ── Tests ────────────────────────────────────────────────────────

    def test_emit_writes_entry_to_correct_path(self, tmp_path, settings):
        """File is created at ``{LOG_ROOT}/{pipeline_id}/orchestrator.log``."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("hello"))

        log_file = tmp_path / "pipe-1" / "orchestrator.log"
        assert log_file.exists()
        assert log_file.is_file()

    def test_emit_entry_has_correct_fields(self, tmp_path, settings):
        """JSON entry contains level, pipeline, stage, src, msg."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("hello", stage="RED", src="orchestrator"))

        entry = json.loads((tmp_path / "pipe-1" / "orchestrator.log").read_text())
        assert entry["level"] == "INFO"
        assert entry["pipeline"] == "pipe-1"
        assert entry["stage"] == "RED"
        assert entry["src"] == "orchestrator"
        assert entry["msg"] == "hello"

    def test_emit_level_appears_as_levelname(self, tmp_path, settings):
        """Different log levels produce the correct level name."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("warn", level=logging.WARNING))
        handler.emit(self._make_record("err", level=logging.ERROR))

        lines = (tmp_path / "pipe-1" / "orchestrator.log").read_text().strip().split("\n")
        assert json.loads(lines[0])["level"] == "WARNING"
        assert json.loads(lines[1])["level"] == "ERROR"

    def test_emit_timestamp_format(self, tmp_path, settings):
        """Timestamp is ISO 8601-like with timezone info."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("x"))

        entry = json.loads((tmp_path / "pipe-1" / "orchestrator.log").read_text())
        ts_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        assert re.match(ts_pattern, entry["ts"])

    def test_emit_no_pipeline_id_skips_write(self, tmp_path, settings):
        """Without pipeline_id the handler returns without writing."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("x", pipeline_id=None))

        assert not list(tmp_path.iterdir())

    def test_emit_includes_ctx_when_present(self, tmp_path, settings):
        """The ``ctx`` field is included in JSON when provided via extra."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("x", ctx={"attempt": 3}))

        entry = json.loads((tmp_path / "pipe-1" / "orchestrator.log").read_text())
        assert entry["ctx"] == {"attempt": 3}

    def test_emit_omits_ctx_when_not_provided(self, tmp_path, settings):
        """Without ctx, no ``ctx`` key appears in the JSON entry."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record("x"))

        entry = json.loads((tmp_path / "pipe-1" / "orchestrator.log").read_text())
        assert "ctx" not in entry

    def test_emit_appends_multiple_entries(self, tmp_path, settings):
        """Multiple calls append lines; no overwrite."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        for i in range(3):
            handler.emit(self._make_record(f"msg {i}"))

        log_file = tmp_path / "pipe-1" / "orchestrator.log"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[0])["msg"] == "msg 0"
        assert json.loads(lines[2])["msg"] == "msg 2"

    def test_emit_all_entries_valid_json(self, tmp_path, settings):
        """Every written line is parseable as JSON."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        for level in (logging.INFO, logging.WARNING, logging.ERROR):
            handler.emit(self._make_record("x", level=level))

        log_file = tmp_path / "pipe-1" / "orchestrator.log"
        for line in log_file.read_text().strip().split("\n"):
            json.loads(line)  # raises if not valid JSON

    def test_emit_with_empty_string_msg(self, tmp_path, settings):
        """Empty msg is written and appears as an empty string."""
        settings.LOG_ROOT = str(tmp_path)
        handler = PipelineFileHandler()
        handler.emit(self._make_record(""))

        entry = json.loads((tmp_path / "pipe-1" / "orchestrator.log").read_text())
        assert entry["msg"] == ""


# ═══════════════════════════════════════════════════════════════════════
#  _write_orchestrator_log_entry — unit tests
# ═══════════════════════════════════════════════════════════════════════


class TestWriteOrchestratorLogEntry:
    """Tests for the ``_write_orchestrator_log_entry`` bridge function.

    These tests attach a capture handler to the ``orchestrator.pipeline``
    logger, call the function, and assert on the captured ``LogRecord``.
    The capture handler is removed after each test to avoid leaking
    between tests.
    """

    @pytest.fixture
    def capture(self):
        """Attach a ``_RecordCaptureHandler`` and yield it for assertions."""
        logger = logging.getLogger("orchestrator.pipeline")
        # Save existing handlers and install our capture handler
        old_handlers = logger.handlers[:]
        logger.handlers.clear()
        handler = _RecordCaptureHandler()
        logger.addHandler(handler)
        yield handler
        # Restore original handlers
        logger.handlers.clear()
        for h in old_handlers:
            logger.addHandler(h)

    def test_sends_msg_at_correct_level(self, capture):
        """Passes through an INFO-level message correctly."""
        _write_orchestrator_log_entry("p1", "INFO", "test message")
        assert len(capture.records) == 1
        assert capture.records[0].levelno == logging.INFO
        assert capture.records[0].getMessage() == "test message"

    def test_maps_WARN_to_WARNING(self, capture):
        """Legacy ``WARN`` level maps to Python's ``WARNING``."""
        _write_orchestrator_log_entry("p1", "WARN", "warn")
        assert capture.records[0].levelno == logging.WARNING

    def test_maps_ERROR_to_ERROR(self, capture):
        _write_orchestrator_log_entry("p1", "ERROR", "err")
        assert capture.records[0].levelno == logging.ERROR

    def test_unknown_level_defaults_to_INFO(self, capture):
        """An unrecognised level string falls back to INFO."""
        _write_orchestrator_log_entry("p1", "UNKNOWN", "fallback")
        assert capture.records[0].levelno == logging.INFO

    def test_sets_pipeline_id_in_extra(self, capture):
        """The pipeline_id is set as a custom attribute on the record."""
        _write_orchestrator_log_entry("pipe-42", "INFO", "msg")
        assert capture.records[0].pipeline_id == "pipe-42"

    def test_default_stage_and_src(self, capture):
        """When omitted, stage defaults to ``-`` and src to ``orchestrator``."""
        _write_orchestrator_log_entry("p1", "INFO", "msg")
        record = capture.records[0]
        assert record.stage == "-"
        assert record.src == "orchestrator"

    def test_custom_stage_and_src(self, capture):
        """Explicit stage and src are passed through."""
        _write_orchestrator_log_entry("p1", "INFO", "msg", stage="RED", src="tester")
        record = capture.records[0]
        assert record.stage == "RED"
        assert record.src == "tester"

    def test_includes_ctx_in_extra(self, capture):
        """A provided ctx dict appears as a custom attribute."""
        _write_orchestrator_log_entry("p1", "INFO", "msg", ctx={"key": "val"})
        assert capture.records[0].ctx == {"key": "val"}

    def test_omits_ctx_when_not_provided(self, capture):
        """When ctx is None, no ctx attribute is set on the record."""
        _write_orchestrator_log_entry("p1", "INFO", "msg")
        record = capture.records[0]
        assert not hasattr(record, "ctx") or record.ctx is None

    def test_case_insensitive_level(self, capture):
        """Level strings are case-insensitive (uppercased internally)."""
        _write_orchestrator_log_entry("p1", "info", "lowercase")
        assert capture.records[0].levelno == logging.INFO


# ═══════════════════════════════════════════════════════════════════════
#  Constants — kept as-is
# ═══════════════════════════════════════════════════════════════════════


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
