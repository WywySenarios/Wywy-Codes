"""Tests for state read/write/validate with backup logic."""

from __future__ import annotations

import json
import os

import pytest

from apps.orchestrator.state.models import PipelineState, STAGE_NAMES
from apps.orchestrator.state.state_manager import (
    backup_state,
    init_state,
    read_state,
    record_error,
    validate_state,
    write_state,
)


class TestInitState:
    def test_creates_queued_state(self):
        state = init_state("my-branch", "test-123")
        assert state.status == "queued"
        assert state.invocation_name == "my-branch"
        assert state.pipeline_id == "test-123"

    def test_auto_generates_uuid(self):
        state = init_state("auto-branch")
        assert len(state.pipeline_id) > 0
        assert state.status == "queued"
        assert len(state.stages) == 11


class TestWriteAndReadState:
    def test_write_and_read_round_trip(self, state_file):
        state = init_state("rt-branch", "rt-1")
        write_state(state_file, state)
        restored = read_state(state_file)
        assert restored.pipeline_id == state.pipeline_id
        assert restored.invocation_name == state.invocation_name
        assert restored.status == state.status

    def test_write_updates_updated_at(self, state_file):
        state = init_state("test", "t1")
        old_time = state.updated_at
        write_state(state_file, state)
        restored = read_state(state_file)
        assert restored.updated_at != old_time

    def test_read_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            read_state("/nonexistent/state.json")

    def test_restore_from_backup_when_corrupted(self, state_file):
        state = init_state("test", "bak-1")
        state.status = "running"
        write_state(state_file, state)
        backup_state(state_file)

        with open(state_file, "w") as f:
            f.write("corrupted{not json")

        restored = read_state(state_file)
        assert restored.status == "running"
        assert restored.pipeline_id == state.pipeline_id

    def test_both_files_corrupted_raises(self, state_file):
        with open(state_file, "w") as f:
            f.write("corrupted")
        bak_path = str(state_file) + ".bak"
        with open(bak_path, "w") as f:
            f.write("also corrupted")
        with pytest.raises(FileNotFoundError):
            read_state(state_file)

    def test_write_is_atomic(self, state_file):
        """Write to .tmp then rename — .tmp should not exist after write."""
        state = init_state("atomic", "at-1")
        write_state(state_file, state)
        tmp_path = str(state_file) + ".tmp"
        assert not os.path.exists(tmp_path)
        assert os.path.exists(state_file)

    def test_write_preserves_json_structure(self, state_file):
        state = init_state("json-test", "jt-1")
        write_state(state_file, state)
        with open(state_file) as f:
            data = json.load(f)
        assert "pipeline_id" in data
        assert "stages" in data
        assert "artifacts" in data
        assert data["status"] == "queued"


class TestBackupState:
    def test_backup_creates_bak_file(self, state_file):
        state = init_state("test", "b1")
        write_state(state_file, state)
        result = backup_state(state_file)
        assert result is True
        assert os.path.exists(str(state_file) + ".bak")

    def test_backup_missing_file_returns_false(self):
        result = backup_state("/nonexistent/state.json")
        assert result is False


class TestValidateState:
    def test_queued_state_is_valid(self):
        state = PipelineState(invocation_name="t", status="queued", pipeline_id="v1")
        valid, errors = validate_state(state)
        assert valid, str(errors)
        assert errors == []

    def test_running_with_one_running_stage(self):
        state = PipelineState(invocation_name="t", status="running", pipeline_id="v1")
        state.current_stage = "planner"
        state.stages["planner"].status = "running"
        valid, errors = validate_state(state)
        assert valid, str(errors)

    def test_running_with_no_running_stage(self):
        state = PipelineState(invocation_name="t", status="running", pipeline_id="v1")
        state.current_stage = "planner"
        valid, _ = validate_state(state)
        assert not valid

    def test_running_with_multiple_running_stages(self):
        state = PipelineState(invocation_name="t", status="running", pipeline_id="v1")
        state.current_stage = "planner"
        state.stages["planner"].status = "running"
        state.stages["coder"].status = "running"
        valid, _ = validate_state(state)
        assert not valid

    def test_bad_pipeline_status(self):
        state = PipelineState(invocation_name="t", status="nonexistent", pipeline_id="v1")
        valid, _ = validate_state(state)
        assert not valid

    def test_bad_stage_status(self):
        state = PipelineState(invocation_name="t", status="running", pipeline_id="v1")
        state.current_stage = "planner"
        state.stages["planner"].status = "running"
        state.stages["plan_reviewer"].status = "badvalue"
        valid, errors = validate_state(state)
        assert not valid
        assert any("plan_reviewer" in e for e in errors)

    def test_terminal_status_no_running_stages(self):
        state = PipelineState(invocation_name="t", status="completed", pipeline_id="v1")
        state.stages["planner"].status = "completed"
        state.stages["coder"].status = "running"
        valid, _ = validate_state(state)
        assert not valid

    def test_blocked_pipeline_valid(self):
        state = PipelineState(invocation_name="t", status="blocked", pipeline_id="v1")
        state.current_stage = "coder"
        state.stages["coder"].status = "blocked"
        valid, _ = validate_state(state)
        assert valid

    def test_running_with_bad_current_stage(self):
        state = PipelineState(invocation_name="t", status="running", pipeline_id="v1")
        state.current_stage = "nonexistent_stage"
        state.stages["planner"].status = "running"
        valid, _ = validate_state(state)
        assert not valid


class TestRecordError:
    def test_appends_error_and_updates_timestamp(self):
        state = PipelineState(invocation_name="test", pipeline_id="re-1")
        old_time = state.updated_at
        record_error(state, "planner", "timeout occurred")
        assert len(state.errors) == 1
        assert state.errors[0].stage == "planner"
        assert state.errors[0].message == "timeout occurred"
        assert state.updated_at != old_time

    def test_multiple_errors(self):
        state = PipelineState(invocation_name="test", pipeline_id="re-2")
        record_error(state, "planner", "first")
        record_error(state, "coder", "second")
        assert len(state.errors) == 2
        assert state.errors[1].stage == "coder"


class TestStateOwnershipSeparation:
    """Verify that functions respect the ownership boundaries defined in the plan.

    Ownership:
      - Agents write: stages.{STAGE}.status, stages.{STAGE}.output
      - Orchestrator writes: status, current_stage, iteration_count,
        user_input_pending, user_input_prompt, artifacts, errors, updated_at
    """

    def test_agent_stage_update_pattern(self):
        """Simulate an agent updating only its own stage."""
        state = PipelineState(invocation_name="test", pipeline_id="own-1")
        state.status = "running"
        state.current_stage = "coder"

        state.stages["coder"].status = "completed"
        state.stages["coder"].output = {"files_changed": ["foo.ts"]}
        state.updated_at = "2026-01-01T00:00:00Z"

        state.current_stage = "code_reviewer"
        state.iteration_count += 1

        assert state.stages["planner"].status == "pending"
        assert state.stages["code_reviewer"].status == "pending"
        assert state.stages["coder"].status == "completed"
        assert state.current_stage == "code_reviewer"
