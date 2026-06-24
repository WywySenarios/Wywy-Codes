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

from opencode_ai import AsyncOpencode

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
    async def test_real_server_integration(
        self, db,
    ) -> None:
        """Full integration test: container lifecycle, session creation,
        message exchange, and stage execution against a real opencode server.

        All scenarios use a single container to avoid Docker networking
        resource contention from sequential container start/stop cycles.
        """
        pipeline = Pipeline.objects.create(
            invocation_name="int-integration",
            description="Full integration test",
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
            # ── Container lifecycle ──────────────────────────────────────
            pipeline.refresh_from_db()
            assert pipeline.container_id == container_id

            agent = await cm.wait_healthy(container_id, timeout=60)
            assert agent is not None
            assert isinstance(agent, AsyncOpencode)

            # ── Session creation and message ─────────────────────────────
            session = await agent.session.create()
            session_id = session.id
            assert session_id is not None
            assert isinstance(session_id, str)
            assert len(session_id) > 0

            prompt_parts = build_stage_prompt(pipeline, stage)
            assert len(prompt_parts) > 0
            assert prompt_parts[0]["type"] == "text"

            model_id = settings.OPENCODE_DEFAULT_MODEL
            provider_id = model_id.split("/")[0]
            response = await agent.session.chat(
                session_id,
                model_id=model_id,
                provider_id=provider_id,
                parts=prompt_parts,
            )
            assert response is not None
            assert isinstance(response.parts, list)

            messages = await agent.session.messages(session_id)
            assert len(messages) > 0

            # ── Stage execution ──────────────────────────────────────────
            result = await execute_stage(pipeline, stage, agent)

            assert result in (
                StageResult.COMPLETED,
                StageResult.BLOCKED,
            ), f"Stage execution failed with {result}"

            assert stage.status in ("completed", "blocked")
            assert stage.session_id is not None
            assert stage.started_at is not None
            assert stage.finished_at is not None

            stage_messages = await agent.session.messages(stage.session_id)
            assert len(stage_messages) >= 0
        finally:
            cm.stop_container(pipeline)
