"""Tests for POST /api/pipelines/<uuid:id>/respond/ endpoint.

The session-based respond flow:
- Look up the blocked stage (``status="blocked"``) on the pipeline
- Send the user's response as a follow-up message via ``AsyncOpencode.session.chat()``
- Clear ``pipeline.user_input_pending``
- Call ``wake_orchestrator()``
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRespond:
    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/respond/"

    @patch("apps.orchestrator.views.wake_orchestrator")
    def test_respond_sends_session_message(
        self, mock_wake, client, db, pipeline_blocked_with_session,
    ) -> None:
        """Happy path: user responds → message sent to opencode session
        → ``user_input_pending`` cleared → ``wake_orchestrator`` called."""
        with patch("apps.orchestrator.views.AsyncOpencode", create=True) as MockAsyncOpencode:
            mock_client = MockAsyncOpencode.return_value
            mock_client.session.chat = AsyncMock(
                return_value=MagicMock(error=None),
            )

            response = client.post(
                self.url(pipeline_blocked_with_session),
                data=json.dumps({"freeform_response": "use red please"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # AsyncOpencode was constructed
        MockAsyncOpencode.assert_called_once()

        # session.chat was called with the blocked stage's session_id,
        # model, provider, and the user's response as a text part.
        mock_client.session.chat.assert_called_once()
        call_kwargs = mock_client.session.chat.call_args.kwargs
        assert call_kwargs["parts"] == [{"type": "text", "text": "use red please"}]
        assert call_kwargs["model_id"] is not None
        assert call_kwargs["provider_id"] is not None

        # user_input_pending was cleared in the database
        pipeline_blocked_with_session.refresh_from_db()
        assert pipeline_blocked_with_session.user_input_pending is False

        # wake_orchestrator was called
        mock_wake.assert_called_once()

    @patch("apps.orchestrator.views.wake_orchestrator")
    def test_respond_with_selected_option(
        self, mock_wake, client, db, pipeline_blocked_with_session,
    ) -> None:
        """When ``selected_option`` is provided, it is prepended to the
        message text sent to the session."""
        with patch("apps.orchestrator.views.AsyncOpencode", create=True) as MockAsyncOpencode:
            mock_client = MockAsyncOpencode.return_value
            mock_client.session.chat = AsyncMock(
                return_value=MagicMock(error=None),
            )

            response = client.post(
                self.url(pipeline_blocked_with_session),
                data=json.dumps({
                    "selected_option": "red",
                    "freeform_response": "more details",
                }),
                content_type="application/json",
            )

        assert response.status_code == 200

        # selected_option is prepended to the text
        assert mock_client.session.chat.call_count == 1
        parts = mock_client.session.chat.call_args.kwargs["parts"]
        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert "[Selected option: red]" in parts[0]["text"]
        assert "more details" in parts[0]["text"]

    def test_respond_no_session_id_returns_400(
        self, client, db, pipeline_blocked_wo_session,
    ) -> None:
        """When the blocked stage has no ``session_id``, the endpoint
        returns 400 — the orchestrator cannot deliver the message."""
        response = client.post(
            self.url(pipeline_blocked_wo_session),
            data=json.dumps({"freeform_response": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "session" in response.json()["error"].lower()

    def test_respond_to_non_pending_pipeline(
        self, client, db, pipeline_queued,
    ) -> None:
        """A pipeline that is not awaiting input returns 400."""
        response = client.post(
            self.url(pipeline_queued),
            data=json.dumps({"freeform_response": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "not awaiting user input" in response.json()["error"]

    def test_invalid_json_returns_400(
        self, client, db, pipeline_blocked_with_session,
    ) -> None:
        """Invalid JSON body returns 400 before any session logic."""
        response = client.post(
            self.url(pipeline_blocked_with_session),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON"

    def test_404_for_nonexistent_pipeline(self, client, db) -> None:
        """Unknown pipeline UUID returns 404."""
        response = client.post(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/respond/",
            data=json.dumps({"freeform_response": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_rejects_non_post_methods(
        self, client, db, pipeline_blocked_with_session,
    ) -> None:
        """GET (and other methods) return 405."""
        response = client.get(self.url(pipeline_blocked_with_session))
        assert response.status_code == 405
