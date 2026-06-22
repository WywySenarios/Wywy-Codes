"""Integration test for the blocked user-input flow.

Tests the chain:
1. ``execute_stage`` produces a blocked result (with ``session_id``)
2. ``api_respond`` sends the user's response to the opencode session
3. Pipeline's ``user_input_pending`` is cleared so the orchestrator resumes
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from apps.orchestrator.agent_client import MessageResponse
from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.orchestrator import STAGE_ORDER


class TestBlockedFlow:
    """End-to-end test of the blocked → respond → resume flow."""

    def test_blocked_stage_is_responded_and_pipeline_resumes(
        self,
        client,
        db,
    ) -> None:
        """A pipeline with a blocked stage (as ``execute_stage`` would leave it)
        receives user input via ``POST /respond/``, which sends the message to
        the opencode session and clears ``user_input_pending``."""
        # ── Simulate the DB state left by execute_stage(BLOCKED) ──────────
        pipeline = Pipeline.objects.create(
            invocation_name="blocked-flow-integration",
            description="Blocked → respond → resume",
            status="running",
            current_stage="GREEN",
            user_input_pending=True,
        )
        for name in STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
        # Init and RED completed; GREEN blocked with a session_id
        PipelineStage.objects.filter(pipeline=pipeline, name="init").update(status="completed")
        PipelineStage.objects.filter(pipeline=pipeline, name="RED").update(status="completed")
        PipelineStage.objects.filter(pipeline=pipeline, name="GREEN").update(
            status="blocked",
            session_id="sess_blocked_integration",
        )

        # ── Act: user responds via the API ─────────────────────────────────
        with patch("apps.orchestrator.views.AgentClient", create=True) as MockAgentClient:
            mock_client = MockAgentClient.return_value
            mock_client.send_message = AsyncMock(
                return_value=MessageResponse(id="resp_1", parts=[]),
            )
            with patch("apps.orchestrator.views.wake_orchestrator"):
                response = client.post(
                    f"/api/pipelines/{pipeline.id}/respond/",
                    data=json.dumps({"freeform_response": "continue please"}),
                    content_type="application/json",
                )

        # ── Assert ─────────────────────────────────────────────────────────
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # The follow-up message was sent to the correct session
        mock_client.send_message.assert_called_once_with(
            "sess_blocked_integration",
            parts=[{"type": "text", "text": "continue please"}],
        )

        # Pipeline is no longer awaiting user input → orchestrator resumes
        pipeline.refresh_from_db()
        assert pipeline.user_input_pending is False
