"""Container lifecycle manager for opencode server containers.

Manages starting, stopping, and health-checking the Docker containers
that run the opencode ``serve`` process for each pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import docker
from django.conf import settings
from opencode_ai import AsyncOpencode

from apps.orchestrator import orchestrator

logger = logging.getLogger(__name__)


class ContainerManager:
    """Manages the lifecycle of opencode server containers.

    Each pipeline gets a single persistent ``opencode serve`` container
    that stages communicate with via the HTTP API.
    """

    def start_container(self, pipeline) -> str:
        """Start an opencode serve container for the given pipeline.

        Synchronous — the Docker SDK and Django ORM calls both run in the
        caller's thread.  Only ``wait_healthy()`` is async (genuinely needs
        ``await`` for the health-polling loop).

        Returns the container's short ID and persists it on the pipeline
        record in the database.
        """
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)

        # ── Build volumes dict ──────────────────────────────────────────
        volumes: dict[str, dict] = {}
        copies_dir = workspace / "copies"
        for repo in orchestrator.REPO_CONFIG:
            repo_path = copies_dir / repo["mount"].lstrip("/")
            volumes[str(repo_path)] = {"bind": repo["mount"], "mode": "rw"}
        volumes.update({
            str(workspace / "state"): {"bind": "/state", "mode": "rw"},
            str(workspace / "artifacts"): {"bind": "/artifacts", "mode": "rw"},
            str(workspace / "context"): {"bind": "/context", "mode": "rw"},
            str(log_dir): {"bind": "/logs", "mode": "rw"},
        })

        # ── Build environment dict ──────────────────────────────────────
        environment = {
            "PIPELINE_ID": str(pipeline.id),
            "HOME": "/home/wywy",
            "OPENCODE_SERVER_PASSWORD": settings.OPENCODE_SERVER_PASSWORD,
            "OPENCODE_WARMUP": "1" if settings.OPENCODE_WARMUP else "0",
            "OPENCODE_API_KEY": getattr(settings, "AGENT_OPENCODE_API_KEY", ""),
            "DEEPSEEK_API_KEY": getattr(settings, "AGENT_DEEPSEEK_API_KEY", ""),
            "OPENAI_API_KEY": getattr(settings, "AGENT_OPENAI_API_KEY", ""),
            "ANTHROPIC_API_KEY": getattr(settings, "AGENT_ANTHROPIC_API_KEY", ""),
        }

        # ── Spawn container ─────────────────────────────────────────────
        client = docker.from_env()
        container = client.containers.run(
            image=settings.AGENT_IMAGE,
            command=[
                "opencode", "serve",
                "--port", str(settings.OPENCODE_SERVER_PORT),
                "--hostname", settings.OPENCODE_SERVER_HOSTNAME,
            ],
            environment=environment,
            volumes=volumes,
            user=f":{settings.AGENT_CONTAINER_GID}",
            detach=True,
            network=settings.AGENT_NETWORK,
        )

        # ── Persist container ID ────────────────────────────────────────
        pipeline.container_id = container.short_id
        pipeline.save(update_fields=["container_id", "updated_at"])

        return container.short_id

    def stop_container(self, pipeline) -> None:
        """Stop and remove the opencode serve container for the pipeline.

        Synchronous (see ``start_container`` docstring for reasoning).
        """
        client = docker.from_env()
        container = client.containers.get(pipeline.container_id)
        container.stop()
        container.remove(force=True)

    async def wait_healthy(
        self, container_id: str, timeout: float = 30
    ) -> AsyncOpencode:
        """Poll the container's health endpoint until it responds 200.

        Returns the ``AsyncOpencode`` instance used to check health.
        Raises ``TimeoutError`` if the server does not become healthy
        within the configured timeout.

        This method is genuinely async — ``docker.from_env()`` and
        ``container.reload()`` are offloaded to ``asyncio.to_thread``
        (they don't touch the database), while ``await
        agent._client.get()`` and ``await asyncio.sleep()`` respect the
        event loop.
        """
        client = await asyncio.to_thread(docker.from_env)
        container = await asyncio.to_thread(
            client.containers.get, container_id
        )
        await asyncio.to_thread(container.reload)
        ip = container.attrs["NetworkSettings"]["Networks"][
            settings.AGENT_NETWORK
        ]["IPAddress"]
        base_url = f"http://{ip}:{settings.OPENCODE_SERVER_PORT}"
        password = settings.OPENCODE_SERVER_PASSWORD
        agent = AsyncOpencode(
            base_url=base_url,
            timeout=300.0,
            max_retries=2,
            default_headers={"Authorization": f"Bearer {password}"}
            if password
            else None,
        )

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Container {container_id} did not become healthy "
                    f"within {timeout}s"
                )
            resp = await agent._client.get("/global/health")
            healthy = resp.status_code == 200
            if healthy:
                # Server is running — optionally warm up the model so
                # the first real request doesn't pay the cold-start
                # penalty.  Controlled by the OPENCODE_WARMUP setting.
                if settings.OPENCODE_WARMUP:
                    await self._warmup_model(agent)
                return agent
            await asyncio.sleep(1)

    async def _warmup_model(self, agent: AsyncOpencode) -> None:
        """Send a warm-up message to trigger model inference.

        Best-effort — failures are logged but not propagated.
        """
        try:
            warmup_session = await agent.session.create()
            if warmup_session:
                logger.info(
                    "Warming up model (session %s)", warmup_session.id
                )
                model_id = settings.OPENCODE_DEFAULT_MODEL
                provider_id = model_id.split("/")[0]
                await agent.session.chat(
                    warmup_session.id,
                    model_id=model_id,
                    provider_id=provider_id,
                    parts=[
                        {
                            "type": "text",
                            "text": "Respond with one word: ready",
                        }
                    ],
                )
                logger.info("Model warm-up complete")
        except Exception:
            logger.warning("Model warm-up failed (non-fatal)")
