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
            "error_message", "iteration_count", "user_input_pending",
            "user_input_request", "pr_url", "description",
            "created_at", "updated_at",
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

    def test_null_user_input_request_preserved(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test", user_input_request=None)
        data = pipeline_to_dict(pipeline)
        assert data["user_input_request"] is None

    def test_user_input_request_with_data(self, db):
        pipeline = Pipeline.objects.create(
            invocation_name="test",
            user_input_request={"question": "what?", "options": ["a", "b"]},
        )
        data = pipeline_to_dict(pipeline)
        assert data["user_input_request"] == {"question": "what?", "options": ["a", "b"]}

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


class TestStageToDict:
    def test_all_fields_present(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="planner")
        data = stage_to_dict(stage)

        expected_keys = {"id", "name", "status", "output", "retry_count", "started_at", "finished_at"}
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
