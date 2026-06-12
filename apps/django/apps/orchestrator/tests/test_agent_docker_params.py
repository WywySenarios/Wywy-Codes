"""Deep Docker-level test: verify server container parameters.

_start_opencode_server spawns exactly one persistent opencode serve
container per pipeline.  This test captures that invocation and verifies
image, command, environment, volumes, user, network, and detach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline


class MockContainer:
    short_id = "abc12345"

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        return {"StatusCode": 0}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return b""

    def remove(self, force: bool = True) -> None:
        pass

    def reload(self) -> None:
        pass


class MockContainers:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> MockContainer:
        self.run_calls.append(kwargs)
        return MockContainer()


class TestOpencodeServerDockerParams:

    def test_server_container_spawned_with_correct_params(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        pipeline = Pipeline.objects.create(
            invocation_name="server-param-test",
            description="Docker params test for server container",
            status="queued",
        )
        orchestrator._create_workspace(pipeline)
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)

        mock_containers = MockContainers()
        mock_client = type("MockClient", (), {"containers": mock_containers})()
        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)

        orchestrator._start_opencode_server(pipeline)

        assert len(mock_containers.run_calls) == 1, (
            f"Expected 1 server container spawn, got {len(mock_containers.run_calls)}"
        )
        kwargs = mock_containers.run_calls[0]

        # --- image ---
        assert kwargs.get("image") == settings.AGENT_IMAGE

        # --- detach ---
        assert kwargs.get("detach") is True

        # --- network ---
        assert kwargs.get("network") == settings.AGENT_NETWORK

        # --- user ---
        assert kwargs.get("user") == f":{settings.AGENT_CONTAINER_GID}"

        # --- command: opencode serve ---
        command = kwargs.get("command", [])
        assert command[0] == "opencode"
        assert command[1] == "serve"
        assert "--port" in command
        assert "--hostname" in command

        # --- environment ---
        env = kwargs.get("environment", {})
        assert env.get("PIPELINE_ID") == str(pipeline.id)
        assert env.get("HOME") == "/home/wywy"
        assert "OPENCODE_SERVER_PASSWORD" in env
        for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                     "ANTHROPIC_API_KEY", "OPENCODE_API_KEY"):
            assert key in env, f"Missing env var {key}"

        # --- volumes ---
        volumes: dict[str, dict[str, str]] = kwargs.get("volumes", {})
        copies_dir = workspace / "copies"
        for repo in orchestrator.REPO_CONFIG:
            repo_path = copies_dir / repo["mount"].lstrip("/")
            if repo_path.exists():
                vol = volumes[str(repo_path)]
                assert vol.get("bind") == repo["mount"]
                assert vol.get("mode") == "rw"

        vol_binds = {v.get("bind"): v for v in volumes.values() if "bind" in v}
        for mount in ("/state", "/artifacts", "/context", "/logs"):
            assert mount in vol_binds, f"Missing volume {mount}"
            assert vol_binds[mount].get("mode") == "rw"

        # Verify no /workspace/.opencode read-only mount (server handles its own config)
        assert "/workspace/.opencode" not in vol_binds, (
            "Server container should not have /workspace/.opencode mount"
        )
