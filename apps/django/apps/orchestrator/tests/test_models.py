"""Tests for Pipeline and PipelineStage Django models."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.orchestrator.models import Pipeline, PipelineStage


class TestPipelineDefaults:
    def test_default_status_is_queued(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.status == "queued"

    def test_default_iteration_count_is_zero(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.iteration_count == 0

    def test_default_user_input_pending_is_false(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.user_input_pending is False

    def test_default_description_is_empty(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.description == ""

    def test_created_at_is_set_on_creation(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.created_at is not None

    def test_updated_at_is_set_on_creation(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.updated_at is not None

    def test_id_is_auto_generated_uuid(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        assert pipeline.id is not None
        assert len(str(pipeline.id)) == 36  # UUID string length

    def test_id_is_unique(self, db):
        p1 = Pipeline.objects.create(invocation_name="test-1")
        p2 = Pipeline.objects.create(invocation_name="test-2")
        assert p1.id != p2.id


class TestPipelineStageConstraints:
    def test_unique_together_pipeline_name(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        PipelineStage.objects.create(pipeline=pipeline, name="planner")
        with pytest.raises(IntegrityError):
            PipelineStage.objects.create(pipeline=pipeline, name="planner")

    def test_same_name_different_pipelines_allowed(self, db):
        p1 = Pipeline.objects.create(invocation_name="p1")
        p2 = Pipeline.objects.create(invocation_name="p2")
        PipelineStage.objects.create(pipeline=p1, name="planner")
        PipelineStage.objects.create(pipeline=p2, name="planner")

    def test_cascade_delete(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        PipelineStage.objects.create(pipeline=pipeline, name="planner")
        PipelineStage.objects.create(pipeline=pipeline, name="coder")
        assert pipeline.stages.count() == 2

        pipeline.delete()
        assert PipelineStage.objects.filter(pipeline_id=pipeline.id).count() == 0

    def test_default_stage_status_is_pending(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="coder")
        assert stage.status == "pending"

    def test_default_retry_count_is_zero(self, db):
        pipeline = Pipeline.objects.create(invocation_name="test")
        stage = PipelineStage.objects.create(pipeline=pipeline, name="coder")
        assert stage.retry_count == 0
