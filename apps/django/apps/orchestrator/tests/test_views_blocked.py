"""Tests for GET /api/pipelines/blocked/ endpoint."""

from __future__ import annotations

import json

from apps.orchestrator.models import Pipeline


class TestListBlockedPipelines:
    URL = "/api/pipelines/blocked/"

    def test_empty_when_no_blocked(self, client, db):
        Pipeline.objects.create(invocation_name="p1", status="running", user_input_pending=False)
        Pipeline.objects.create(invocation_name="p2", status="queued", user_input_pending=False)

        response = client.get(self.URL)
        assert response.status_code == 200
        assert response.json()["pipelines"] == []

    def test_returns_only_blocked_pipelines(self, client, db):
        Pipeline.objects.create(invocation_name="p1", status="running", user_input_pending=False)
        Pipeline.objects.create(invocation_name="p2", status="running", user_input_pending=True)
        Pipeline.objects.create(invocation_name="p3", status="running", user_input_pending=True)

        response = client.get(self.URL)
        pipelines = response.json()["pipelines"]
        assert len(pipelines) == 2
        names = {p["invocation_name"] for p in pipelines}
        assert names == {"p2", "p3"}

    def test_rejects_non_get_methods(self, client, db):
        response = client.post(self.URL, data=json.dumps({}), content_type="application/json")
        assert response.status_code == 405

    def test_blocked_pipeline_has_user_input_request(self, client, db):
        Pipeline.objects.create(
            invocation_name="blocked",
            status="running",
            user_input_pending=True,
            user_input_request={"question": "what?"},
        )
        response = client.get(self.URL)
        pipeline = response.json()["pipelines"][0]
        assert pipeline["user_input_pending"] is True
        assert pipeline["user_input_request"] == {"question": "what?"}
