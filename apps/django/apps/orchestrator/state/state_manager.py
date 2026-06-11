"""State file read/write with atomic writes and backup/restore.

Ownership rules:
  - Agents write: stages.{STAGE}.status and stages.{STAGE}.output
  - Orchestrator writes: status, current_stage, iteration_count,
    user_input_pending, user_input_prompt, artifacts, errors, updated_at

Write atomicity: write to .tmp, then os.rename (atomic on same filesystem).
Backup: before each stage start, orchestrator copies state.json → state.json.bak.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apps.orchestrator.state.models import (
    PipelineState,
    StageState,
    ErrorEntry,
    VALID_STATUSES,
    TERMINAL_STATUSES,
    STAGE_NAMES,
)


def read_state(path: str | Path) -> PipelineState:
    """Read and parse state.json, restoring from backup if corrupted.

    Args:
        path: Full path to state.json.

    Returns:
        A PipelineState instance.

    Raises:
        FileNotFoundError: If neither state.json nor state.json.bak exist.
        ValueError: If both state.json and the backup fail to parse.
    """
    state_path = Path(path)
    bak_path = Path(str(state_path) + ".bak")

    for attempt_path in (state_path, bak_path):
        try:
            with open(attempt_path) as f:
                data = json.load(f)
            return PipelineState.from_dict(data)
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, TypeError):
            # State is corrupted; fall through to backup
            continue

    raise FileNotFoundError(
        f"Neither {state_path} nor {bak_path} found or both are unparseable"
    )


def write_state(path: str | Path, state: PipelineState) -> None:
    """Atomically write pipeline state to state.json.

    Writes to a temporary file and renames, which is atomic on the same
    filesystem. Updates ``updated_at`` to the current UTC time.

    Args:
        path: Full path to state.json.
        state: PipelineState to persist.
    """
    state_path = Path(path)
    tmp_path = Path(str(state_path) + ".tmp")

    state.updated_at = datetime.now(timezone.utc).isoformat()

    with open(tmp_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.rename(tmp_path, state_path)


def backup_state(path: str | Path) -> bool:
    """Copy state.json to state.json.bak before a stage starts.

    Call this from the orchestrator before advancing to the next stage.

    Args:
        path: Full path to state.json.

    Returns:
        True if backup succeeded, False if state.json didn't exist.
    """
    state_path = Path(path)
    bak_path = Path(str(state_path) + ".bak")

    if not state_path.exists():
        return False

    with open(state_path) as src:
        data = src.read()
    with open(bak_path, "w") as dst:
        dst.write(data)
    return True


def validate_state(state: PipelineState) -> tuple[bool, list[str]]:
    """Validate a PipelineState for correctness.

    Checks:
      - Status values are valid
      - Pipeline in terminal state has no running stages
      - Pipeline running implies at least one stage running
      - current_stage references a real stage (if pipeline is running/blocked)

    Args:
        state: The PipelineState to validate.

    Returns:
        A tuple of (is_valid, list_of_error_messages).
    """
    errors: list[str] = []

    # Pipeline status must be valid
    if state.status not in VALID_STATUSES:
        errors.append(
            f"Invalid pipeline status '{state.status}'. "
            f"Must be one of: {', '.join(sorted(VALID_STATUSES))}"
        )

    # current_stage must reference a real stage (when pipeline is active)
    if state.status in ("running", "blocked"):
        if state.current_stage not in STAGE_NAMES:
            errors.append(
                f"current_stage '{state.current_stage}' is not a valid stage name"
            )

    # Terminal status means no running stages
    if state.status in TERMINAL_STATUSES:
        for name, stage in state.stages.items():
            if stage.status in ("running", "blocked"):
                errors.append(
                    f"Pipeline is {state.status} but stage '{name}' "
                    f"is still {stage.status}"
                )

    # Stage statuses must be valid
    for name, stage in state.stages.items():
        if stage.status not in VALID_STATUSES:
            errors.append(
                f"Stage '{name}' has invalid status '{stage.status}'"
            )

    # Running pipeline should have exactly one running stage
    if state.status == "running":
        running_stages = [
            name
            for name, stage in state.stages.items()
            if stage.status == "running"
        ]
        if len(running_stages) == 0:
            errors.append(
                "Pipeline is running but no stage is marked as running"
            )
        elif len(running_stages) > 1:
            errors.append(
                f"Multiple stages marked as running: {running_stages}"
            )

    return len(errors) == 0, errors


def init_state(
    invocation_name: str, pipeline_id: Optional[str] = None
) -> PipelineState:
    """Create a fresh PipelineState with queued status.

    Args:
        invocation_name: User-provided branch name.
        pipeline_id: Optional override UUID (auto-generated if None).

    Returns:
        A new PipelineState ready for initial write.
    """
    state = PipelineState(invocation_name=invocation_name, status="queued")
    if pipeline_id is not None:
        state.pipeline_id = pipeline_id
    return state


def record_error(
    state: PipelineState, stage: str, message: str
) -> None:
    """Append an error entry to the state and update the timestamp.

    Args:
        state: PipelineState to mutate.
        stage: Stage name where the error occurred.
        message: Human-readable error description.
    """
    state.errors.append(ErrorEntry(stage=stage, message=message))
    state.updated_at = datetime.now(timezone.utc).isoformat()
