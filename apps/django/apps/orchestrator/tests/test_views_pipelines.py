"""Tests for the POST /api/pipelines/ (create) and GET /api/pipelines/ (list) endpoints."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.orchestrator.models import Pipeline


class TestCreatePipeline:
    URL = "/api/pipelines/"

    def test_creates_pipeline_and_returns_201(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "my-feature"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["invocation_name"] == "my-feature"
        assert data["status"] == "queued"
        assert "id" in data
        assert Pipeline.objects.filter(invocation_name="my-feature").exists()

    def test_description_is_optional_and_preserved(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "test", "description": "my desc"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.json()["description"] == "my desc"

    def test_description_defaults_to_empty_string(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.json()["description"] == ""

    def test_missing_invocation_name_returns_400(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"description": "no name"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invocation_name is required"

    def test_empty_invocation_name_returns_400(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invocation_name is required"

    def test_uppercase_rejected(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "MyFeature"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "lowercase" in response.json()["error"]

    def test_spaces_rejected(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "my feature"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "lowercase" in response.json()["error"]

    def test_special_chars_rejected(self, client, db):
        for name in ["my!feature", "my@feature", "my#feature", "my.feature"]:
            response = client.post(
                self.URL,
                data=json.dumps({"invocation_name": name}),
                content_type="application/json",
            )
            assert response.status_code == 400, f"Expected 400 for '{name}'"

    def test_valid_chars_accepted(self, client, db):
        for name in ["my-feature", "my_feature", "abc123", "a", "a-1_b-2"]:
            response = client.post(
                self.URL,
                data=json.dumps({"invocation_name": name}),
                content_type="application/json",
            )
            assert response.status_code == 201, f"Expected 201 for '{name}': {response.json()}"

    def test_invalid_json_returns_400(self, client, db):
        response = client.post(
            self.URL,
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON"

    def test_empty_body_returns_400(self, client, db):
        response = client.post(
            self.URL,
            data="",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON"

    @patch("apps.orchestrator.views.wake_orchestrator")
    def test_wake_orchestrator_called_on_success(self, mock_wake, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        mock_wake.assert_called_once()

    @patch("apps.orchestrator.views.wake_orchestrator")
    def test_wake_orchestrator_not_called_on_failure(self, mock_wake, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "Bad Name"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        mock_wake.assert_not_called()

    def test_created_pipeline_is_in_database(self, client, db):
        response = client.post(
            self.URL,
            data=json.dumps({"invocation_name": "persist-me"}),
            content_type="application/json",
        )
        pipeline_id = response.json()["id"]
        pipeline = Pipeline.objects.get(pk=pipeline_id)
        assert pipeline.status == "queued"
        assert pipeline.invocation_name == "persist-me"


class TestListPipelines:
    URL = "/api/pipelines/"

    def test_empty_list_returns_empty_array(self, client, db):
        response = client.get(self.URL)
        assert response.status_code == 200
        assert response.json()["pipelines"] == []

    def test_returns_all_pipelines(self, client, db):
        Pipeline.objects.create(invocation_name="p1")
        Pipeline.objects.create(invocation_name="p2")
        response = client.get(self.URL)
        assert len(response.json()["pipelines"]) == 2

    def test_status_filter_single(self, client, db):
        Pipeline.objects.create(invocation_name="queued", status="queued")
        Pipeline.objects.create(invocation_name="running", status="running")
        Pipeline.objects.create(invocation_name="completed", status="completed")

        response = client.get(self.URL + "?status=queued")
        pipelines = response.json()["pipelines"]
        assert len(pipelines) == 1
        assert pipelines[0]["invocation_name"] == "queued"

    def test_status_filter_multiple(self, client, db):
        Pipeline.objects.create(invocation_name="queued", status="queued")
        Pipeline.objects.create(invocation_name="running", status="running")
        Pipeline.objects.create(invocation_name="completed", status="completed")

        response = client.get(self.URL + "?status=queued,running")
        pipelines = response.json()["pipelines"]
        assert len(pipelines) == 2
        names = {p["invocation_name"] for p in pipelines}
        assert names == {"queued", "running"}

    def test_status_filter_matches_none(self, client, db):
        Pipeline.objects.create(invocation_name="queued", status="queued")

        response = client.get(self.URL + "?status=failed")
        assert response.json()["pipelines"] == []

    def test_response_structure(self, client, db):
        Pipeline.objects.create(invocation_name="test", description="desc")
        response = client.get(self.URL)
        data = response.json()["pipelines"][0]
        assert "id" in data
        assert data["invocation_name"] == "test"
        assert data["description"] == "desc"
        assert data["status"] == "queued"
