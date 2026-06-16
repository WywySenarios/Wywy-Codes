"""Tests for GET /api/pipelines/<uuid:id>/ (detail) endpoint."""

from __future__ import annotations

import json

from apps.orchestrator.models import Pipeline, PipelineStage


class TestPipelineDetail:
    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/"

    def test_returns_pipeline_data(self, client, db, pipeline_queued):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(pipeline_queued.id)
        assert data["invocation_name"] == pipeline_queued.invocation_name
        assert data["status"] == "queued"

    def test_includes_stages(self, client, db, pipeline_running):
        response = client.get(self.url(pipeline_running))
        assert response.status_code == 200
        data = response.json()
        assert "stages" in data
        assert len(data["stages"]) == 6
        stage_names = {s["name"] for s in data["stages"]}
        assert "RED" in stage_names
        assert "GREEN" in stage_names

    def test_stage_has_all_fields(self, client, db, pipeline_running):
        response = client.get(self.url(pipeline_running))
        stage = response.json()["stages"][0]
        assert "id" in stage
        assert "name" in stage
        assert "status" in stage
        assert "retry_count" in stage

    def test_404_for_nonexistent_pipeline(self, client, db):
        response = client.get("/api/pipelines/00000000-0000-0000-0000-000000000000/")
        assert response.status_code == 404

    def test_rejects_non_get_methods(self, client, db, pipeline_queued):
        response = client.post(self.url(pipeline_queued), data=json.dumps({}), content_type="application/json")
        assert response.status_code == 405

    def test_no_stages_for_queued_pipeline(self, client, db, pipeline_queued):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        assert response.json()["stages"] == []

    def test_detail_stages_sorted(self, client, db):
        pipeline = Pipeline.objects.create(invocation_name="sorted", status="running")
        PipelineStage.objects.create(pipeline=pipeline, name="RED", status="completed")
        PipelineStage.objects.create(pipeline=pipeline, name="GREEN", status="pending")

        response = client.get(self.url(pipeline))
        stages = response.json()["stages"]
        assert stages[0]["name"] == "RED"  # ordered by id (creation order)
        assert stages[1]["name"] == "GREEN"
