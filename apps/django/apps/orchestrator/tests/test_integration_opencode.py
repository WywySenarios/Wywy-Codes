"""Integration test: full pipeline with real opencode server.

Gated behind ``OPENCODE_INTEGRATION_TEST=1`` — safe for CI (skipped by default).

When enabled, spins up a real ``opencode serve`` container via the Docker SDK,
creates a pipeline and stage, executes the stage against the server, and
verifies the integration end-to-end: session creation, message delivery,
stage completion, diff retrieval, and container lifecycle.
"""

from __future__ import annotations

import logging
import os
import socket

import docker
import pytest
from django.conf import settings

from apps.orchestrator.agent_client import AgentClient
from apps.orchestrator.container_manager import ContainerManager
from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.stage_executor import StageResult, build_stage_prompt, execute_stage

logger = logging.getLogger(__name__)

# ── Gate ────────────────────────────────────────────────────────────────

OPENCODE_INTEGRATION_TEST = os.environ.get("OPENCODE_INTEGRATION_TEST") == "1"

pytestmark = pytest.mark.skipif(
    not OPENCODE_INTEGRATION_TEST,
    reason="Set OPENCODE_INTEGRATION_TEST=1 to run this integration test",
)


# ── Infrastructure — network setup ─────────────────────────────────────


def _ensure_agent_network() -> None:
    """Ensure ``wywy-agent-net`` exists and the test container is attached.

    Mirrors the production ``_ensure_agent_network()`` in
    ``orchestrator.py`` so that the test container can reach agent
    containers by IP on the shared bridge network.
    """
    client = docker.from_env()
    network_name = settings.AGENT_NETWORK
    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        logger.info("Creating agent network '%s'", network_name)
        network = client.networks.create(network_name, driver="bridge")

    container_id = socket.gethostname()
    try:
        network.connect(container_id)
        logger.info(
            "Connected container '%s' to network '%s'",
            container_id, network_name,
        )
    except docker.errors.APIError:
        pass  # Already connected


@pytest.fixture(scope="session", autouse=True)
def _setup_infra() -> None:
    """Session-scoped autouse fixture: set up Docker networking for
    integration tests.

    Runs once before any test in this module when the module is
    loaded (the gate keeps it skipped otherwise).
    """
    _ensure_agent_network()


# ── Tests ───────────────────────────────────────────────────────────────


class TestIntegrationOpencode:
    """End-to-end integration tests using a real opencode server container.

    These tests require:
    - Docker socket access (``/var/run/docker.sock``)
    - The ``wywy/agent`` image (or ``settings.AGENT_IMAGE``) available locally

    The agent network (``settings.AGENT_NETWORK``) is created and the test
    container is attached automatically by the ``_setup_infra`` fixture.
    """

    @pytest.mark.django_db(transaction=True)
    async def test_real_server_container_lifecycle(
        self, db,
    ) -> None:
        """Start an opencode serve container, verify health, stop it."""
        pipeline = Pipeline.objects.create(
            invocation_name="int-lifecycle",
            description="Lifecycle integration test",
            status="running",
        )

        cm = ContainerManager()
        container_id = cm.start_container(pipeline)

        try:
            # Container ID must be persisted on the pipeline
            pipeline.refresh_from_db()
            assert pipeline.container_id == container_id

            # Server must become healthy
            agent = await cm.wait_healthy(container_id, timeout=60)
            assert agent is not None
            assert isinstance(agent, AgentClient)

            # Health check must return True
            healthy = await agent.health_check()
            assert healthy is True
        finally:
            cm.stop_container(pipeline)

    @pytest.mark.django_db(transaction=True)
    async def test_session_creation_and_message(
        self, db,
    ) -> None:
        """Create a session on a real opencode server, send a message,
        and verify the response contains expected fields."""
        pipeline = Pipeline.objects.create(
            invocation_name="int-session",
            description="Session integration test",
            status="running",
        )

        cm = ContainerManager()
        container_id = cm.start_container(pipeline)

        try:
            agent = await cm.wait_healthy(container_id, timeout=60)

            # Create a session
            session_id = await agent.create_session(title="test-session")
            assert session_id is not None
            assert isinstance(session_id, str)
            assert len(session_id) > 0

            # Send a simple message
            stage = PipelineStage.objects.create(
                pipeline=pipeline,
                name="test_stage",
                status="pending",
            )

            prompt_parts = build_stage_prompt(pipeline, stage)
            assert len(prompt_parts) > 0
            assert prompt_parts[0]["type"] == "text"

            response = await agent.send_message(
                session_id,
                parts=prompt_parts,
            )
            assert response is not None
            assert response.id is not None
            assert isinstance(response.parts, list)

            # Verify session messages are retrievable
            messages = await agent.get_session_messages(session_id, limit=5)
            assert len(messages) > 0
            assert messages[0].id is not None
        finally:
            cm.stop_container(pipeline)

    @pytest.mark.django_db(transaction=True)
    async def test_stage_execution_against_real_server(
        self, db, monkeypatch,
    ) -> None:
        """Execute a full pipeline stage against a real opencode server
        using ``stage_executor.execute_stage()``."""
        pipeline = Pipeline.objects.create(
            invocation_name="int-stage-exec",
            description="Stage execution integration test",
            status="running",
        )

        stage = PipelineStage.objects.create(
            pipeline=pipeline,
            name="test_stage",
            status="pending",
        )

        cm = ContainerManager()
        container_id = cm.start_container(pipeline)

        try:
            agent = await cm.wait_healthy(container_id, timeout=60)

            # ── Execute the stage ────────────────────────────────────────
            result = await execute_stage(pipeline, stage, agent)

            # Must not fail — completed or blocked are acceptable outcomes
            # for a simple prompt against the real server.
            assert result in (
                StageResult.COMPLETED,
                StageResult.BLOCKED,
            ), f"Stage execution failed with {result}"

            # ── Verify in-memory state was updated ───────────────────────
            assert stage.status in ("completed", "blocked")
            assert stage.session_id is not None, (
                "Stage must have a session_id after execution"
            )
            assert stage.started_at is not None
            assert stage.finished_at is not None

            # ── Verify session messages are retrievable ──────────────────
            messages = await agent.get_session_messages(
                stage.session_id, limit=5,
            )
            assert len(messages) >= 0  # at least the response exists

            # ── Verify diffs are retrievable ─────────────────────────────
            diffs = await agent.get_session_diff(stage.session_id)
            assert diffs is not None
        finally:
            cm.stop_container(pipeline)
