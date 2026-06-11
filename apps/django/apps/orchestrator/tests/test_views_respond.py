"""Tests for POST /api/pipelines/<uuid:id>/respond/ endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings


class TestRespond:
    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/respond/"

    @patch("apps.orchestrator.views.write_user_input_response")
    def test_respond_success(self, mock_write, client, db, pipeline_awaiting_input, temp_workspace):
        response = client.post(
            self.url(pipeline_awaiting_input),
            data=json.dumps({"freeform_response": "use red please"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_write.assert_called_once()

    @patch("apps.orchestrator.views.write_user_input_response")
    def test_respond_with_selected_option(self, mock_write, client, db, pipeline_awaiting_input, temp_workspace):
        response = client.post(
            self.url(pipeline_awaiting_input),
            data=json.dumps({"selected_option": "red", "freeform_response": "more details"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        called_pipeline, called_text = mock_write.call_args[0]
        assert "[Selected option: red]" in called_text
        assert "more details" in called_text

    @patch("apps.orchestrator.views.write_user_input_response")
    def test_respond_creates_response_file(self, mock_write, client, db, pipeline_awaiting_input, temp_workspace):
        response = client.post(
            self.url(pipeline_awaiting_input),
            data=json.dumps({"freeform_response": "do it"}),
            content_type="application/json",
        )
        assert response.status_code == 200

        workspace = Path(settings.WORKSPACE_ROOT)
        input_dir = workspace / str(pipeline_awaiting_input.id) / "context" / "user-input"
        assert input_dir.is_dir()
        files = list(input_dir.glob("response_*.md"))
        assert len(files) == 1
        assert files[0].read_text() == "do it"

    def test_respond_to_non_pending_pipeline(self, client, db, pipeline_queued, temp_workspace):
        response = client.post(
            self.url(pipeline_queued),
            data=json.dumps({"freeform_response": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "not awaiting user input" in response.json()["error"]

    def test_invalid_json_returns_400(self, client, db, pipeline_awaiting_input, temp_workspace):
        response = client.post(
            self.url(pipeline_awaiting_input),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON"

    def test_404_for_nonexistent_pipeline(self, client, db, temp_workspace):
        response = client.post(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/respond/",
            data=json.dumps({"freeform_response": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_rejects_non_post_methods(self, client, db, pipeline_awaiting_input, temp_workspace):
        response = client.get(self.url(pipeline_awaiting_input))
        assert response.status_code == 405

    @patch("apps.orchestrator.views.write_user_input_response")
    def test_respond_increments_file_counter(self, mock_write, client, db, pipeline_awaiting_input, temp_workspace):
        workspace = Path(settings.WORKSPACE_ROOT)
        input_dir = workspace / str(pipeline_awaiting_input.id) / "context" / "user-input"
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "response_1.md").write_text("first")
        (input_dir / "response_2.md").write_text("second")

        client.post(
            self.url(pipeline_awaiting_input),
            data=json.dumps({"freeform_response": "third"}),
            content_type="application/json",
        )
        assert (input_dir / "response_3.md").exists()
        assert (input_dir / "response_3.md").read_text() == "third"
