"""Structured JSON-lines logging for the agentic pipeline.

Every log entry is a single JSON object per line with correlation fields:
  ts, level, pipeline, stage, src, msg, ctx

Log files at: /var/log/Wywy-Website/agentic/{pipeline_id}/{source}.log
  with a ``latest`` symlink updated by the orchestrator at pipeline start.

Inside containers, logs are written to /logs/{STAGE}.log (append-only).
The STAGE env var tells each agent which filename to use.

Minimum entries per stage (from the plan):
  - Stage start (INFO)
  - Any WARN-level condition
  - Stage completion/failure (INFO/ERROR)
  - Block reason if entering blocked state (INFO)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_BASE_DIR: str = "/var/log/Wywy-Website/agentic"

# Valid values for the ``src`` field in log entries.
VALID_SOURCES: frozenset[str] = frozenset({
    "orchestrator",
    "planner",
    "plan_reviewer",
    "test_builder",
    "coder",
    "code_reviewer",
    "testing",
    "testing_align_red",
    "testing_green_unit",
    "testing_green_integration",
    "integration_e2e_builder",
    "pr_writer",
    "pr_reviewer",
})


def _build_entry(
    level: str,
    pipeline: str,
    stage: str,
    src: str,
    msg: str,
    ctx: Optional[dict] = None,
) -> str:
    """Build a single JSON-lines log entry string (including trailing newline).

    Args:
        level: INFO, WARN, or ERROR.
        pipeline: Pipeline UUID.
        stage: Current pipeline stage name.
        src: Source component (orchestrator, planner, etc.).
        msg: Human-readable event description.
        ctx: Optional extra detail dict.

    Returns:
        A JSON string terminated with a newline.
    """
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    entry: dict[str, object] = {
        "ts": ts,
        "level": level,
        "pipeline": pipeline,
        "stage": stage,
        "src": src,
        "msg": msg,
    }
    if ctx is not None:
        entry["ctx"] = ctx
    return json.dumps(entry) + "\n"


class LogWriter:
    """Append-only writer for a single stage log file.

    Used by the orchestrator and by agent containers (where logs are written
    to /logs/{STAGE}.log).

    Attributes:
        filepath: Full path to the log file.
    """

    def __init__(self, filepath: str | Path):
        self._path = Path(filepath)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def filepath(self) -> Path:
        return self._path

    def write(self, entry: str) -> None:
        """Append a log entry string to the file."""
        with open(self._path, "a") as f:
            f.write(entry)

    def info(
        self,
        pipeline: str,
        stage: str,
        src: str,
        msg: str,
        ctx: Optional[dict] = None,
    ) -> None:
        """Write an INFO-level log entry."""
        self.write(_build_entry("INFO", pipeline, stage, src, msg, ctx))

    def warn(
        self,
        pipeline: str,
        stage: str,
        src: str,
        msg: str,
        ctx: Optional[dict] = None,
    ) -> None:
        """Write a WARN-level log entry."""
        self.write(_build_entry("WARN", pipeline, stage, src, msg, ctx))

    def error(
        self,
        pipeline: str,
        stage: str,
        src: str,
        msg: str,
        ctx: Optional[dict] = None,
    ) -> None:
        """Write an ERROR-level log entry."""
        self.write(_build_entry("ERROR", pipeline, stage, src, msg, ctx))


def log_info(
    pipeline: str, stage: str, src: str, msg: str, ctx: Optional[dict] = None
) -> None:
    """Convenience: write an INFO entry to the orchestrator log.

    Args:
        pipeline: Pipeline UUID.
        stage: Current pipeline stage name.
        src: Source component name.
        msg: Human-readable event description.
        ctx: Optional extra detail dict.
    """
    _orchestrator_log().info(pipeline, stage, src, msg, ctx)


def log_warn(
    pipeline: str, stage: str, src: str, msg: str, ctx: Optional[dict] = None
) -> None:
    """Convenience: write a WARN entry to the orchestrator log."""
    _orchestrator_log().warn(pipeline, stage, src, msg, ctx)


def log_error(
    pipeline: str, stage: str, src: str, msg: str, ctx: Optional[dict] = None
) -> None:
    """Convenience: write an ERROR entry to the orchestrator log."""
    _orchestrator_log().error(pipeline, stage, src, msg, ctx)


_ORCHESTRATOR_LOG: Optional[LogWriter] = None


def _orchestrator_log() -> LogWriter:
    """Get or create the orchestrator's own LogWriter.

    The orchestrator's log is always written to
    ``/var/log/Wywy-Website/agentic/latest/orchestrator.log`` via the
    ``latest`` symlink so it works even before the pipeline_id directory
    is created.
    """
    global _ORCHESTRATOR_LOG
    if _ORCHESTRATOR_LOG is None:
        log_path = Path(LOG_BASE_DIR) / "latest" / "orchestrator.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _ORCHESTRATOR_LOG = LogWriter(log_path)
    return _ORCHESTRATOR_LOG


def ensure_log_directory(pipeline_id: str) -> Path:
    """Create the per-pipeline log directory and set up the latest symlink.

    Args:
        pipeline_id: The pipeline UUID.

    Returns:
        Path to the created log directory.
    """
    log_dir = Path(LOG_BASE_DIR) / pipeline_id
    log_dir.mkdir(parents=True, exist_ok=True)

    latest_link = Path(LOG_BASE_DIR) / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(str(log_dir), target_is_directory=True)

    # Reset cached orchestrator log so it points to the new latest target
    global _ORCHESTRATOR_LOG
    _ORCHESTRATOR_LOG = None

    return log_dir


def tail_log(
    pipeline_id: str,
    stage: str,
    lines: int = 200,
    base_dir: str | Path | None = None,
) -> list[dict[str, object]]:
    """Read the last N lines of a stage log file and parse as JSON.

    Used by the log tailing API endpoint.

    Args:
        pipeline_id: Pipeline UUID.
        stage: Stage name (used as the log filename stem).
        lines: Maximum number of lines to return (default 200).
        base_dir: Override log base directory (defaults to LOG_BASE_DIR).

    Returns:
        A list of parsed JSON objects (one per log entry).
    """
    root = Path(base_dir) if base_dir is not None else Path(LOG_BASE_DIR)
    log_path = root / pipeline_id / f"{stage}.log"
    try:
        with open(log_path) as f:
            raw_lines = f.readlines()[-lines:]
    except FileNotFoundError:
        return []

    entries: list[dict[str, object]] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entries.append(json.loads(stripped))
        except json.JSONDecodeError:
            entries.append({"raw": stripped, "parse_error": True})
    return entries
