"""Pipeline state persistence and structured logging for the agentic pipeline.

This package provides:
  - Dataclass models matching the state.json schema
  - Atomic read/write with backup/restore
  - State validation rules
  - Structured JSON-lines logging helpers
"""

from apps.orchestrator.state.state_manager import read_state, write_state, validate_state, init_state
from apps.orchestrator.state.logging import (
    LogWriter,
    log_info,
    log_warn,
    log_error,
    ensure_log_directory,
    LOG_BASE_DIR,
)

__all__ = [
    "read_state",
    "write_state",
    "validate_state",
    "init_state",
    "LogWriter",
    "log_info",
    "log_warn",
    "log_error",
    "ensure_log_directory",
    "LOG_BASE_DIR",
]
