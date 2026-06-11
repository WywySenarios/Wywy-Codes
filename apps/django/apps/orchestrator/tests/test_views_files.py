"""Tests for GET /api/pipelines/<uuid:id>/files/ endpoint."""

from __future__ import annotations

import json
from pathlib import Path


class TestPipelineFiles:
    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/files/"

    @staticmethod
    def create_test_files(workspace: Path, pipeline_id: str):
        ws = workspace / str(pipeline_id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "artifacts").mkdir()
        (ws / "artifacts" / "plan.md").write_text("# Plan")
        (ws / "summary_plan.md").write_text("summary")
        (ws / "orchestrator.log").write_text("log content")
        (ws / "context").mkdir()
        (ws / "context" / "user-input").mkdir(parents=True)
        (ws / "context" / "user-input" / "question_1.md").write_text("what?")
        (ws / "state.json").write_text('{"status":"running"}')

    def test_404_for_nonexistent_pipeline(self, client, db, temp_workspace):
        response = client.get("/api/pipelines/00000000-0000-0000-0000-000000000000/files/")
        assert response.status_code == 404

    def test_rejects_non_get_methods(self, client, db, pipeline_queued, temp_workspace):
        response = client.post(self.url(pipeline_queued))
        assert response.status_code == 405

    def test_returns_file_listing(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        data = response.json()
        assert "artifacts" in data
        assert "summaries" in data
        assert "user_input" in data
        assert "logs" in data

    def test_artifacts_listed(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        artifacts = response.json()["artifacts"]
        paths = [a["path"] for a in artifacts]
        assert "artifacts/plan.md" in paths

    def test_user_input_files_listed(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        files = response.json()["user_input"]
        paths = [f["path"] for f in files]
        assert "context/user-input/question_1.md" in paths

    def test_logs_listed(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        logs = response.json()["logs"]
        paths = [f["path"] for f in logs]
        assert "orchestrator.log" in paths

    def test_summaries_listed(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        summaries = response.json()["summaries"]
        paths = [s["path"] for s in summaries]
        assert "summary_plan.md" in paths

    def test_empty_workspace_returns_empty_categories(self, client, db, pipeline_queued, temp_workspace):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        data = response.json()
        assert data["artifacts"] == []
        assert data["summaries"] == []
        assert data["user_input"] == []
        assert data["logs"] == []
        assert data["other"] == []

    def test_serve_file_content(self, client, db, pipeline_queued, temp_workspace):
        ws = temp_workspace / str(pipeline_queued.id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test.txt").write_text("hello world")

        response = client.get(f"{self.url(pipeline_queued)}?path=test.txt")
        assert response.status_code == 200
        assert response.content.decode() == "hello world"

    def test_serve_json_file(self, client, db, pipeline_queued, temp_workspace):
        ws = temp_workspace / str(pipeline_queued.id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "data.json").write_text('{"key": "value"}')

        response = client.get(f"{self.url(pipeline_queued)}?path=data.json")
        assert response.status_code == 200
        assert response.json() == {"key": "value"}

    def test_file_not_found(self, client, db, pipeline_queued, temp_workspace):
        ws = temp_workspace / str(pipeline_queued.id)
        ws.mkdir(parents=True, exist_ok=True)

        response = client.get(f"{self.url(pipeline_queued)}?path=nonexistent.txt")
        assert response.status_code == 404

    def test_path_traversal_blocked(self, client, db, pipeline_queued, temp_workspace):
        ws = temp_workspace / str(pipeline_queued.id)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "safe.txt").write_text("safe")

        response = client.get(f"{self.url(pipeline_queued)}?path=../../../etc/passwd")
        assert response.status_code == 400
        assert "traversal" in response.json()["error"].lower()

    def test_file_size_included(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(self.url(pipeline_queued))
        for artifact in response.json()["artifacts"]:
            assert "size" in artifact
            assert isinstance(artifact["size"], int)

    def test_verbose_shows_state_files(self, client, db, pipeline_queued, temp_workspace):
        self.create_test_files(temp_workspace, pipeline_queued.id)

        response = client.get(f"{self.url(pipeline_queued)}?verbose=1")
        data = response.json()
        state_files = [f["path"] for f in data["other"]]
        assert "state.json" in state_files
