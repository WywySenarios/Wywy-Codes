"""Tests for pipeline_to_dict and stage_to_dict serializers."""

from __future__ import annotations

from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.serializers import pipeline_to_dict, stage_to_dict


class TestPipelineToDict:
    def test_all_fields_present(self, db):
        pipeline = Pipeline.objects.create(
            invocation_name="test",
            description="A test",
            status="queued",
            pr_url="https://github.com/test/pr",
        )
        data = pipeline_to_dict(pipeline)

        expected_keys = {
            "id", "invocation_name", "status", "current_stage",
            "error_message", "container_id", "iteration_count",
            "user_input_pending", "pr_url",
            "description", "created_at", "updated_at",
        }
        assert set(data.keys()) == expected_keys

    def test_id_is_string(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        data = pipeline_to_dict(pipeline)
        assert isinstance(data["id"], str)
        assert len(data["id"]) == 36

    def test_dates_are_iso_format(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        data = pipeline_to_dict(pipeline)
        # ISO 8601: "2026-06-10T..."
        assert "T" in data["created_at"]
        assert "T" in data["updated_at"]

    def test_null_pr_url_becomes_empty_string(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test", pr_url=None)
        data = pipeline_to_dict(pipeline)
        assert data["pr_url"] == ""

    def test_invocation_name_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="my-feature")
        data = pipeline_to_dict(pipeline)
        assert data["invocation_name"] == "my-feature"

    def test_description_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test", description="desc here")
        data = pipeline_to_dict(pipeline)
        assert data["description"] == "desc here"

    def test_status_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test", status="running")
        data = pipeline_to_dict(pipeline)
        assert data["status"] == "running"

    # ── RED: container_id must be serialized ───────────────────────────

    def test_container_id_in_pipeline_dict(self, db):
        """``pipeline_to_dict`` must include ``container_id`` so the
        frontend can track which opencode server container belongs to
        this pipeline."""
        pipeline = Pipeline.objects.create(invocation_name="test")
        data = pipeline_to_dict(pipeline)
        # This assertion FAILS — container_id is not in the serializer yet.
        assert "container_id" in data, (
            f"pipeline_to_dict must include 'container_id'. "
            f"Got keys: {list(data.keys())}"
        )
        assert data["container_id"] is None, (
            "Default container_id should be None"
        )


class TestStageToDict:
    def test_all_fields_present(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner")
        data = stage_to_dict(stage)

        expected_keys = {
            "id", "name", "status", "output", "retry_count",
            "session_id", "forked_from_session_id",
            "started_at", "finished_at",
        }
        assert set(data.keys()) == expected_keys

    def test_name_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="coder", status="completed")
        data = stage_to_dict(stage)
        assert data["name"] == "coder"

    def test_status_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner", status="completed")
        data = stage_to_dict(stage)
        assert data["status"] == "completed"

    def test_output_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner", output={"result": "ok"})
        data = stage_to_dict(stage)
        assert data["output"] == {"result": "ok"}

    def test_null_dates_remain_none(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner")
        data = stage_to_dict(stage)
        assert data["started_at"] is None
        assert data["finished_at"] is None

    def test_retry_count_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner", retry_count=3)
        data = stage_to_dict(stage)
        assert data["retry_count"] == 3

    # ── RED: session fields must be serialized ─────────────────────────

    def test_session_id_in_stage_dict(self, db):
        """``stage_to_dict`` must include ``session_id`` so the frontend
        can reference the opencode server session for this stage."""
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner")
        data = stage_to_dict(stage)
        # This assertion FAILS — session_id is not in the serializer yet.
        assert "session_id" in data, (
            f"stage_to_dict must include 'session_id'. "
            f"Got keys: {list(data.keys())}"
        )
        assert data["session_id"] is None, (
            "Default session_id should be None"
        )

    def test_forked_from_session_id_in_stage_dict(self, db):
        """``stage_to_dict`` must include ``forked_from_session_id`` so
        the frontend can track retry fork origin."""
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner")
        data = stage_to_dict(stage)
        # This assertion FAILS — forked_from_session_id is not in the serializer yet.
        assert "forked_from_session_id" in data, (
            f"stage_to_dict must include 'forked_from_session_id'. "
            f"Got keys: {list(data.keys())}"
        )
        assert data["forked_from_session_id"] is None, (
            "Default forked_from_session_id should be None"
        )
