"""Tests for the opencode server container lifecycle manager.

The container manager handles starting, stopping, and health-checking
the opencode ``serve`` container that runs alongside each active pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator.models import Pipeline


# ═══════════════════════════════════════════════════════════════════════
# Mock Docker helpers
# ═══════════════════════════════════════════════════════════════════════


class MockContainer:
    """Mimics a docker ``Container`` object for testing."""

    short_id = "abc12345"

    def __init__(self) -> None:
        self.stop_called = False
        self.remove_called = False
        self.remove_force: bool | None = None
        self.attrs = {
            "NetworkSettings": {
                "Networks": {
                    settings.AGENT_NETWORK: {"IPAddress": "172.18.0.42"},
                }
            }
        }

    def stop(self) -> None:
        self.stop_called = True

    def remove(self, force: bool = True) -> None:
        self.remove_called = True
        self.remove_force = force

    def reload(self) -> None:
        pass


class MockContainers:
    """Mimics ``docker.models.containers.ContainerCollection``."""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self._container = MockContainer()

    def run(self, **kwargs: Any) -> MockContainer:
        self.run_calls.append(kwargs)
        return self._container

    def get(self, container_id: str) -> MockContainer:
        return self._container


class MockDockerClient:
    """Mimics the return value of ``docker.from_env()``."""

    def __init__(self) -> None:
        self.containers = MockContainers()


# ═══════════════════════════════════════════════════════════════════════
# RED: module must exist
# ═══════════════════════════════════════════════════════════════════════


def test_container_manager_module_exists() -> None:
    """The ``container_manager`` module must exist."""
    import apps.orchestrator.container_manager  # noqa: F401


def test_container_manager_class_exists() -> None:
    """The module must expose the ``ContainerManager`` class."""
    from apps.orchestrator.container_manager import ContainerManager  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════
# RED: start_container
# ═══════════════════════════════════════════════════════════════════════


def test_start_container_runs_with_correct_image_command_network_user_detach(
    db: None,
    patched_copy_sources: dict[str, str],
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``start_container`` must call ``containers.run()`` with the expected
    image, command, network, user, and detach=True."""
    from apps.orchestrator import orchestrator
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="container-test",
        description="Container manager test",
        status="running",
    )
    orchestrator._create_workspace(pipeline)

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    mgr.start_container(pipeline)

    assert len(mock_client.containers.run_calls) == 1
    kwargs = mock_client.containers.run_calls[0]

    assert kwargs["image"] == settings.AGENT_IMAGE
    assert kwargs["detach"] is True
    assert kwargs["network"] == settings.AGENT_NETWORK
    assert kwargs["user"] == f":{settings.AGENT_CONTAINER_GID}"

    command = kwargs.get("command", [])
    assert command[0] == "opencode"
    assert command[1] == "serve"
    assert "--port" in command
    assert "--hostname" in command


def test_start_container_includes_correct_volumes(
    db: None,
    patched_copy_sources: dict[str, str],
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``start_container`` must mount the 5 repo copies, state, artifacts,
    context, and logs directories."""
    from apps.orchestrator import orchestrator
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="vol-test",
        description="Volume test",
        status="running",
    )
    orchestrator._create_workspace(pipeline)
    workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    mgr.start_container(pipeline)

    kwargs = mock_client.containers.run_calls[0]
    volumes: dict[str, dict[str, str]] = kwargs.get("volumes", {})

    # ── Repo volumes (each should be mounted if copies exist) ───
    copies_dir = workspace / "copies"
    for repo in orchestrator.REPO_CONFIG:
        repo_path = copies_dir / repo["mount"].lstrip("/")
        vol = volumes.get(str(repo_path))
        assert vol is not None, f"Missing volume for {repo['mount']}"
        assert vol["bind"] == repo["mount"]
        assert vol["mode"] == "rw"

    # ── Fixed volumes ───────────────────────────────────────────
    vol_binds = {v["bind"]: v for v in volumes.values()}
    for mount in ("/workspace/state", "/artifacts", "/context", "/logs"):
        assert mount in vol_binds, f"Missing volume for {mount}"
        assert vol_binds[mount]["mode"] == "rw"


def test_start_container_includes_correct_environment(
    db: None,
    patched_copy_sources: dict[str, str],
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``start_container`` must pass API keys, server password, pipeline ID,
    and HOME in the environment."""
    from apps.orchestrator import orchestrator
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="env-test",
        description="Env test",
        status="running",
    )
    orchestrator._create_workspace(pipeline)

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    mgr.start_container(pipeline)

    kwargs = mock_client.containers.run_calls[0]
    env: dict[str, str] = kwargs.get("environment", {})

    assert env.get("PIPELINE_ID") == str(pipeline.id)
    assert env.get("HOME") == "/home/wywy"
    assert "OPENCODE_SERVER_PASSWORD" in env
    for key in (
        "OPENCODE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert key in env, f"Missing env var {key}"


def test_start_container_returns_container_id(
    db: None,
    patched_copy_sources: dict[str, str],
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``start_container`` must return the container's short ID."""
    from apps.orchestrator import orchestrator
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="return-test",
        description="Return value test",
        status="running",
    )
    orchestrator._create_workspace(pipeline)

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    container_id = mgr.start_container(pipeline)

    assert container_id == MockContainer.short_id


def test_start_container_saves_container_id_to_pipeline(
    db: None,
    patched_copy_sources: dict[str, str],
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """``start_container`` must persist the container ID on the pipeline
    record in the database."""
    from apps.orchestrator import orchestrator
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="save-test",
        description="Save ID test",
        status="running",
    )
    orchestrator._create_workspace(pipeline)

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    mgr.start_container(pipeline)

    pipeline.refresh_from_db()
    assert pipeline.container_id == MockContainer.short_id


# ═══════════════════════════════════════════════════════════════════════
# RED: stop_container
# ═══════════════════════════════════════════════════════════════════════


def test_stop_container_calls_stop_then_remove(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``stop_container`` must call ``container.stop()`` then
    ``container.remove(force=True)``."""
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="stop-test",
        description="Stop test",
        status="running",
        container_id="abc12345",
    )

    mock_container = MockContainer()
    mock_containers = MockContainers()
    mock_containers._container = mock_container
    mock_client = MockDockerClient()
    mock_client.containers = mock_containers
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    mgr = ContainerManager()
    mgr.stop_container(pipeline)

    assert mock_container.stop_called is True
    assert mock_container.remove_called is True
    assert mock_container.remove_force is True


def test_stop_container_looks_up_container_by_pipeline_container_id(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``stop_container`` must look up the container using
    ``pipeline.container_id``."""
    from apps.orchestrator.container_manager import ContainerManager

    pipeline = Pipeline.objects.create(
        invocation_name="lookup-test",
        description="Lookup test",
        status="running",
        container_id="spez_42",
    )

    mock_containers = MockContainers()
    mock_client = MockDockerClient()
    mock_client.containers = mock_containers
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    # Track which container_id is passed to get()
    get_calls: list[str] = []
    original_get = mock_containers.get

    def tracking_get(container_id: str) -> MockContainer:
        get_calls.append(container_id)
        return original_get(container_id)

    mock_containers.get = tracking_get

    mgr = ContainerManager()
    mgr.stop_container(pipeline)

    assert get_calls == ["spez_42"]


# ═══════════════════════════════════════════════════════════════════════
# RED: wait_healthy
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_wait_healthy_returns_sdk_client_when_healthy(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``wait_healthy`` must poll ``/global/health`` and return an
    ``AsyncOpencode`` when the server responds 200."""
    from apps.orchestrator.container_manager import ContainerManager

    # Mock Docker — container with known IP
    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    # Mock AsyncOpencode — health via _client.get, warmup via session
    mock_agent = AsyncMock()
    mock_agent._client.get = AsyncMock(
        return_value=MagicMock(status_code=200),
    )
    mock_agent.session.create = AsyncMock(
        return_value=MagicMock(id="warmup-session"),
    )
    mock_agent.session.chat = AsyncMock()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.AsyncOpencode",
        lambda base_url, timeout=None, max_retries=None,
               default_headers=None: mock_agent,
    )

    mgr = ContainerManager()
    result = await mgr.wait_healthy("abc12345", timeout=5)

    assert result is mock_agent


@pytest.mark.django_db(transaction=True)
async def test_wait_healthy_polls_several_times_before_succeeding(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``wait_healthy`` must poll ``_client.get`` multiple times and
    only return once it succeeds."""
    from apps.orchestrator.container_manager import ContainerManager

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    # Return 503 once, then 200 — proves retry loop
    responses = [
        MagicMock(status_code=503),
        MagicMock(status_code=200),
    ]
    mock_agent = AsyncMock()
    mock_agent._client.get = AsyncMock(side_effect=responses)
    mock_agent.session.create = AsyncMock(
        return_value=MagicMock(id="warmup-session"),
    )
    mock_agent.session.chat = AsyncMock()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.AsyncOpencode",
        lambda base_url, timeout=None, max_retries=None,
               default_headers=None: mock_agent,
    )

    mgr = ContainerManager()
    result = await mgr.wait_healthy("abc12345", timeout=5)

    assert result is mock_agent
    assert mock_agent._client.get.await_count == 2


@pytest.mark.django_db(transaction=True)
async def test_wait_healthy_raises_timeout_error_on_exhaustion(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``wait_healthy`` must raise ``TimeoutError`` when the server does
    not become healthy within the configured timeout."""
    from apps.orchestrator.container_manager import ContainerManager

    mock_client = MockDockerClient()
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.docker.from_env",
        lambda: mock_client,
    )

    # Always unhealthy — triggers timeout
    mock_agent = AsyncMock()
    mock_agent._client.get = AsyncMock(
        return_value=MagicMock(status_code=503),
    )
    monkeypatch.setattr(
        "apps.orchestrator.container_manager.AsyncOpencode",
        lambda base_url, timeout=None, max_retries=None,
               default_headers=None: mock_agent,
    )

    mgr = ContainerManager()
    with pytest.raises(TimeoutError):
        await mgr.wait_healthy("abc12345", timeout=0.1)
