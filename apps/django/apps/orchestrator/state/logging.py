"""Structured JSON-lines logging for the agentic pipeline.

Every log entry is a single JSON object per line with correlation fields:
  ts, level, pipeline, stage, src, msg, ctx

Log files at: /var/log/Wywy-Website/agentic/{pipeline_id}/{source}.log

This module provides:

* ``PipelineFileHandler`` — a :class:`logging.Handler` that writes
  per-pipeline ``orchestrator.log`` files.  Used by Django's ``LOGGING``
  config so all orchestrator log entries go through Python's stdlib logging.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_BASE_DIR: str = "/var/log/Wywy-Website/agentic"

# Valid values for the ``src`` field in log entries.
VALID_SOURCES: frozenset[str] = frozenset({
    "orchestrator",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compilance",
    "PR writer",
    "testing",
})


class PipelineFileHandler(logging.Handler):
    """Logging handler that writes per-pipeline orchestrator.log files.

    Expects ``pipeline_id`` in the log record's ``extra`` dict (set by
    the logger call).  Writes to::

        {LOG_ROOT}/{pipeline_id}/orchestrator.log

    Falls back to ``LOG_BASE_DIR`` when ``settings.LOG_ROOT`` is not yet
    configured (defensive guard only — Django settings are always ready
    by the time ``emit()`` runs in production).
    """

    def __init__(self) -> None:
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            pipeline_id = getattr(record, "pipeline_id", None)
            if not pipeline_id:
                return  # not a pipeline log entry, skip

            # Late import avoids circular dependency at module load time.
            from django.conf import settings

            log_root = getattr(settings, "LOG_ROOT", LOG_BASE_DIR)
            log_dir = Path(log_root) / pipeline_id
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "orchestrator.log"

            entry: dict[str, object] = {
                "ts": datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat(),
                "level": record.levelname,
                "pipeline": pipeline_id,
                "stage": getattr(record, "stage", "-"),
                "src": getattr(record, "src", "orchestrator"),
                "msg": record.getMessage(),
            }
            ctx = getattr(record, "ctx", None)
            if ctx is not None:
                entry["ctx"] = ctx

            with open(log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            self.handleError(record)


LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _write_orchestrator_log_entry(
    pipeline_id: str,
    level: str,
    msg: str,
    *,
    stage: str = "-",
    src: str = "orchestrator",
    ctx: Optional[dict] = None,
) -> None:
    """Write a structured log entry through the ``orchestrator.pipeline`` logger.

    Thin helper that bridges the legacy string-based level / positional-arg
    call sites to Python's logging system.  Production code should call
    the logger directly instead.

    Args:
        pipeline_id: Pipeline UUID.
        level: One of ``DEBUG``, ``INFO``, ``WARN``, ``ERROR``, ``CRITICAL``.
        msg: Human-readable event description.
        stage: Current pipeline stage name (or ``"-"`` when ``None``).
        src: Source component identifier.
        ctx: Optional extra context dict.
    """
    logger = logging.getLogger("orchestrator.pipeline")
    levelno = LEVEL_MAP.get(level.upper(), logging.INFO)
    extra: dict[str, object] = {
        "pipeline_id": pipeline_id,
        "stage": stage,
        "src": src,
    }
    if ctx is not None:
        extra["ctx"] = ctx
    logger.log(levelno, msg, extra=extra)

