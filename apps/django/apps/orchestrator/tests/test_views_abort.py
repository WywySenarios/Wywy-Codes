"""Tests for POST /api/pipelines/<uuid:id>/abort/ endpoint."""

from __future__ import annotations

from unittest.mock import patch


class TestAbort:
    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/abort/"

    @patch("apps.orchestrator.views.abort_pipeline")
    def test_abort_queued_pipeline(self, mock_abort, client, db, pipeline_queued):
        response = client.post(self.url(pipeline_queued))
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_abort.assert_called_once()

    @patch("apps.orchestrator.views.abort_pipeline")
    def test_abort_running_pipeline(self, mock_abort, client, db, pipeline_running):
        response = client.post(self.url(pipeline_running))
        assert response.status_code == 200
        mock_abort.assert_called_once()

    def test_abort_completed_pipeline_returns_400(self, client, db, pipeline_completed):
        response = client.post(self.url(pipeline_completed))
        assert response.status_code == 400
        assert "already" in response.json()["error"]

    def test_abort_failed_pipeline_returns_400(self, client, db, pipeline_failed):
        response = client.post(self.url(pipeline_failed))
        assert response.status_code == 400
        assert "already" in response.json()["error"]

    def test_abort_cancelled_pipeline_returns_400(self, client, db, pipeline_cancelled):
        response = client.post(self.url(pipeline_cancelled))
        assert response.status_code == 400
        assert "already" in response.json()["error"]

    def test_404_for_nonexistent_pipeline(self, client, db):
        response = client.post(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/abort/",
        )
        assert response.status_code == 404

    def test_rejects_non_post_methods(self, client, db, pipeline_queued):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 405
