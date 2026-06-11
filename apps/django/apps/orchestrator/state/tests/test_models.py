"""Tests for state dataclass models (PipelineState, StageState, Artifacts)."""

from __future__ import annotations

import json
import re

from apps.orchestrator.state.models import (
    Artifacts,
    ErrorEntry,
    PipelineState,
    STAGE_NAMES,
    StageState,
    TERMINAL_STATUSES,
    VALID_STATUSES,
)


class TestStageState:
    def test_defaults(self):
        s = StageState()
        assert s.status == "pending"
        assert s.output is None
        assert s.retry_count == 0

    def test_custom_values(self):
        s = StageState(status="completed", output={"result": "ok"}, retry_count=2)
        assert s.status == "completed"
        assert s.output == {"result": "ok"}
        assert s.retry_count == 2


class TestArtifacts:
    def test_defaults(self):
        a = Artifacts()
        assert a.plan == "artifacts/plan.md"
        assert a.spec == "artifacts/spec.md"
        assert a.tests == "artifacts/tests/"
        assert a.integration_tests == "artifacts/integration_tests/"
        assert a.e2e_tests == "artifacts/e2e_tests/"
        assert a.pr_payload == "artifacts/pr_payload.json"


class TestErrorEntry:
    def test_default_timestamp(self):
        e = ErrorEntry(stage="coder", message="crash")
        assert e.stage == "coder"
        assert e.message == "crash"
        assert e.timestamp is not None
        assert "T" in e.timestamp


class TestPipelineState:
    def test_default_creation(self):
        """Default PipelineState has pending status and 11 stages."""
        p = PipelineState(
            invocation_name="test-branch",
            pipeline_id="11111111-1111-1111-1111-111111111111",
        )
        assert p.status == "pending"
        assert p.invocation_name == "test-branch"
        assert p.pipeline_id == "11111111-1111-1111-1111-111111111111"
        assert len(p.stages) == 11
        for name in STAGE_NAMES:
            assert name in p.stages
        assert p.created_at is not None
        assert p.updated_at is not None

    def test_auto_generated_pipeline_id(self):
        """If no pipeline_id is provided, a UUID is generated."""
        p = PipelineState(invocation_name="test")
        assert p.pipeline_id is not None
        assert len(p.pipeline_id) > 0

    def test_all_stages_have_default_values(self):
        """Every stage in a new pipeline starts as pending."""
        p = PipelineState(invocation_name="test")
        for stage in p.stages.values():
            assert stage.status == "pending"
            assert stage.output is None
            assert stage.retry_count == 0

    def test_to_dict_contains_all_top_level_keys(self):
        """The serialised dict must match the state.json schema."""
        p = PipelineState(
            invocation_name="test",
            pipeline_id="abc-123",
            status="queued",
        )
        d = p.to_dict()

        expected_keys = {
            "pipeline_id",
            "invocation_name",
            "status",
            "current_stage",
            "iteration_count",
            "user_input_pending",
            "user_input_prompt",
            "artifacts",
            "stages",
            "errors",
            "created_at",
            "updated_at",
        }
        assert set(d.keys()) == expected_keys
        assert d["pipeline_id"] == "abc-123"
        assert d["status"] == "queued"
        assert d["stages"]["planner"]["status"] == "pending"

    def test_to_dict_is_json_serializable(self):
        """The output of to_dict must be valid JSON."""
        p = PipelineState(
            invocation_name="test",
            pipeline_id="abc-123",
        )
        p.errors.append(ErrorEntry(stage="coder", message="oops"))
        d = p.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["pipeline_id"] == "abc-123"

    def test_from_dict_minimal(self):
        """from_dict with minimal data fills defaults."""
        p = PipelineState.from_dict({
            "pipeline_id": "min-1",
            "invocation_name": "minimal",
            "status": "queued",
        })
        assert p.pipeline_id == "min-1"
        assert p.invocation_name == "minimal"
        assert p.status == "queued"
        assert len(p.stages) == 11
        assert p.stages["planner"].status == "pending"

    def test_from_dict_full(self):
        """from_dict with a complete dict preserves all fields."""
        data = {
            "pipeline_id": "full-1",
            "invocation_name": "full-test",
            "status": "running",
            "current_stage": "coder",
            "iteration_count": 3,
            "user_input_pending": True,
            "user_input_prompt": "What color?",
            "artifacts": {
                "plan": "artifacts/plan.md",
                "spec": "artifacts/spec.md",
                "tests": "artifacts/tests/",
                "integration_tests": "artifacts/integration_tests/",
                "e2e_tests": "artifacts/e2e_tests/",
                "pr_payload": "artifacts/pr_payload.json",
            },
            "stages": {
                "planner": {"status": "completed", "output": {"done": True}, "retry_count": 1},
            },
            "errors": [
                {"stage": "planner", "message": "timeout", "timestamp": "2026-06-01T00:00:00Z"}
            ],
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }
        p = PipelineState.from_dict(data)
        assert p.status == "running"
        assert p.current_stage == "coder"
        assert p.iteration_count == 3
        assert p.user_input_pending is True
        assert p.user_input_prompt == "What color?"
        assert p.stages["planner"].status == "completed"
        assert p.stages["planner"].output == {"done": True}
        assert p.stages["planner"].retry_count == 1
        assert len(p.errors) == 1
        assert p.errors[0].stage == "planner"
        assert p.errors[0].message == "timeout"
        assert p.stages["plan_reviewer"].status == "pending"

    def test_round_trip_preserves_custom_state(self):
        """to_dict -> from_dict round-trip preserves all custom values."""
        p = PipelineState(
            invocation_name="rt-test",
            pipeline_id="rt-1",
            status="running",
            current_stage="test_builder",
            iteration_count=2,
        )
        p.stages["planner"].status = "completed"
        p.stages["planner"].output = {"result": "plan.md"}
        p.stages["plan_reviewer"].status = "completed"
        p.stages["test_builder"].status = "running"
        p.errors.append(ErrorEntry(stage="planner", message="slow"))

        restored = PipelineState.from_dict(p.to_dict())
        assert restored.pipeline_id == p.pipeline_id
        assert restored.status == "running"
        assert restored.current_stage == "test_builder"
        assert restored.iteration_count == 2
        assert restored.stages["planner"].status == "completed"
        assert restored.stages["planner"].output == {"result": "plan.md"}
        assert restored.stages["plan_reviewer"].status == "completed"
        assert restored.stages["test_builder"].status == "running"
        assert len(restored.errors) == 1


class TestConstants:
    def test_valid_statuses(self):
        expected = {"pending", "queued", "running", "blocked",
                     "completed", "failed", "cancelled"}
        assert set(VALID_STATUSES) == expected

    def test_terminal_statuses(self):
        expected = {"completed", "failed", "cancelled"}
        assert set(TERMINAL_STATUSES) == expected

    def test_stage_order(self):
        assert len(STAGE_NAMES) == 11
        assert STAGE_NAMES[0] == "planner"
        assert STAGE_NAMES[-1] == "pr_reviewer"
