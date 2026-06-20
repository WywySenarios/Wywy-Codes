"""Tests for _ensure_agent_network: the orchestrator container must be connected
to the agent Docker network so it can reach pipeline opencode server containers.
"""

from __future__ import annotations

import socket
from typing import Any

import docker
from _pytest.monkeypatch import MonkeyPatch

from apps.orchestrator import orchestrator


# ── helpers ────────────────────────────────────────────────────────────────


class MockNetwork:
    """Simulates a Docker network object returned by ``client.networks.get()``.

    Records calls to ``connect`` so tests can verify the orchestrator's own
    container was connected to the agent network.
    """

    def __init__(self, network_id: str) -> None:
        self.id = network_id
        self.connect_calls: list[tuple[Any, ...]] = []

    def connect(self, container: str, **kwargs: Any) -> None:
        """Record the connect call for later assertion."""
        self.connect_calls.append((container, kwargs))


class MockNetworks:
    """Simulates ``client.networks`` — returns a ``MockNetwork`` on ``get()``.

    ``get()`` always succeeds (network already exists), which is the common
    case after the first pipeline run.  This lets the test focus on whether
    ``connect`` is called, not on network creation.
    """

    def __init__(self) -> None:
        self.network = MockNetwork("test-net-id")
        self.get_calls: list[str] = []

    def get(self, name: str) -> MockNetwork:
        self.get_calls.append(name)
        return self.network


class MockClient:
    """Simulates ``docker.from_env()`` for use with monkeypatch."""

    def __init__(self) -> None:
        self.networks = MockNetworks()


# ── RED tests ──────────────────────────────────────────────────────────────


class TestEnsureAgentNetworkConnectsOrchestrator:
    """``_ensure_agent_network`` must connect the orchestrator container to the
    agent network so it can reach pipeline opencode server containers.
    """

    def test_ensure_agent_network_must_connect_orchestrator_container(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """After ``_ensure_agent_network`` runs, the orchestrator's own
        container must be connected to the agent network.

        ``_ensure_agent_network`` uses ``docker.from_env()`` to interact
        with the Docker daemon.  This test mocks ``docker.from_env()`` to
        detect whether ``network.connect()`` is called with the orchestrator
        container's hostname (from ``socket.gethostname()``).

        The current code does NOT call ``connect`` at all — this assertion
        will fail.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        mock_client = MockClient()
        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)
        monkeypatch.setattr(socket, "gethostname", lambda: "orchestrator-host-42")

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator._ensure_agent_network()

        # ── Assert ───────────────────────────────────────────────────────
        # The network should have been looked up
        assert "wywy-agent-net" in mock_client.networks.get_calls, (
            "Expected _ensure_agent_network to look up the agent network, "
            f"got get_calls={mock_client.networks.get_calls}"
        )

        assert len(mock_client.networks.network.connect_calls) == 1, (
            f"Expected exactly one network.connect() call to connect the "
            f"orchestrator container to '{orchestrator.settings.AGENT_NETWORK}', "
            f"got {len(mock_client.networks.network.connect_calls)}.  "
            f"_ensure_agent_network currently only ensures the network exists "
            f"but never connects the orchestrator's own container."
        )

        container_arg, kwargs = mock_client.networks.network.connect_calls[0]
        assert container_arg == "orchestrator-host-42", (
            f"Expected network.connect() to be called with the orchestrator "
            f"container hostname 'orchestrator-host-42', "
            f"got container_arg={container_arg!r}"
        )

    def test_ensure_agent_network_connects_after_creating_network(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the agent network does not exist and must be created first,
        the orchestrator container must still be connected after creation.

        This tests the code path where ``client.networks.get()`` raises
        ``docker.errors.NotFound``, triggering network creation via
        ``client.networks.create()``.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        mock_client = MockClient()

        # Simulate network NOT found on first get() call.
        # Must raise docker.errors.NotFound so the except clause in
        # _ensure_agent_network catches it.
        original_get = mock_client.networks.get

        def raising_get(name: str) -> MockNetwork:
            raise docker.errors.NotFound(f"Network {name} not found")

        mock_client.networks.get = raising_get  # type: ignore[assignment]

        # Track network creation
        create_calls: list[str] = []

        def track_create(name: str, **kwargs: Any) -> MockNetwork:
            create_calls.append(name)
            # After creation, the next get() should succeed
            mock_client.networks.get = original_get
            # Return the shared mock network so connect() calls are
            # recorded on mock_client.networks.network.connect_calls.
            return mock_client.networks.network

        mock_client.networks.create = track_create  # type: ignore[assignment]

        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)
        monkeypatch.setattr(socket, "gethostname", lambda: "orchestrator-created")

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator._ensure_agent_network()

        # ── Assert ───────────────────────────────────────────────────────
        # Network should have been created
        assert len(create_calls) == 1, (
            f"Expected network to be created once, "
            f"got create_calls={create_calls}"
        )
        assert "wywy-agent-net" in create_calls, (
            f"Created network should be '{orchestrator.settings.AGENT_NETWORK}', "
            f"got {create_calls}"
        )

        assert len(mock_client.networks.network.connect_calls) == 1, (
            f"Expected network.connect() to be called after creating the "
            f"network, got {len(mock_client.networks.network.connect_calls)}.  "
            f"The orchestrator container must be connected regardless of "
            f"whether the network already existed or was just created."
        )

    def test_ensure_agent_network_skips_connect_if_already_connected(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """If the orchestrator container is already connected to the agent
        network, ``_ensure_agent_network`` must not raise or reconnect.

        Docker's ``network.connect()`` is idempotent (it succeeds silently
        when the container is already connected), so this is primarily a
        safety-net test to document that re-connecting is harmless.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        mock_client = MockClient()

        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)
        monkeypatch.setattr(socket, "gethostname", lambda: "already-connected")

        # ── Act (first call — should connect) ────────────────────────────
        orchestrator._ensure_agent_network()

        # ── Act (second call — should be idempotent) ─────────────────────
        orchestrator._ensure_agent_network()

        # ── Assert ───────────────────────────────────────────────────────
        # Docker's connect is idempotent — calling it twice is harmless.
        assert len(mock_client.networks.network.connect_calls) == 2, (
            f"Expected connect() to be called on every invocation (Docker's "
            f"connect is idempotent), got "
            f"{len(mock_client.networks.network.connect_calls)}.  "
            f"Even on the second call, the function must not raise."
        )

    def test_ensure_agent_network_does_not_raise_on_docker_error(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the Docker daemon is unreachable, ``_ensure_agent_network``
        must catch the exception and return gracefully — it must not crash
        the orchestrator loop thread.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        monkeypatch.setattr(
            orchestrator.docker, "from_env", lambda: (_ for _ in ()).throw(
                orchestrator.docker.errors.DockerException("Cannot connect"),
            ),
        )

        # ── Act — must not raise ─────────────────────────────────────────
        try:
            orchestrator._ensure_agent_network()
        except Exception as exc:
            raise AssertionError(
                f"_ensure_agent_network must catch Docker errors gracefully, "
                f"got {type(exc).__name__}: {exc}"
            ) from exc
