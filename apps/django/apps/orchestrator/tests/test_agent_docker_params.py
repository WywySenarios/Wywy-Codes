"""Deep Docker-level test: capture all 9 container invocations and verify parameters.

Mocks docker.from_env() to capture containers.run(**kwargs) for every stage.
Verifies image, command, environment, volumes, user, network, and detach.
"""

from __future__ import annotations

import json
import time
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


class MockContainers:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> MockContainer:
        self.run_calls.append(kwargs)
        return MockContainer()


class TestAgentDockerParams:

    def test_all_stages_spawned_with_correct_docker_params(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # 1. Create pipeline via ORM
        pipeline = Pipeline.objects.create(
            invocation_name="docker-param-test",
            description="Docker params test for all stages",
            status="queued",
        )

        # 2. Bootstrap pipeline
        orchestrator._create_workspace(pipeline)
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._create_stages(pipeline)
        pipeline.refresh_from_db()

        # 3. Pre-write state.json so _validate_stage_state passes for all stages
        state_path = orchestrator._state_file_path(pipeline)
        state = json.loads(state_path.read_text())
        for name in orchestrator.STAGE_ORDER:
            state.setdefault("stages", {})[name] = {"status": "completed"}
        state["updated_at"] = time.time()
        tmp = Path(str(state_path) + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(state_path)

        # 4. Mock docker.from_env() to capture containers.run() calls
        mock_containers = MockContainers()
        mock_client = type("MockClient", (), {"containers": mock_containers})()

        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)

        # 5. Mock external side-effects
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # 6. Run — single advance_pipeline chains through all 9 stages
        orchestrator.advance_pipeline(pipeline)

        # 7. Verify 9 container calls captured
        run_calls = mock_containers.run_calls
        assert len(run_calls) == 9, (
            f"Expected 9 container spawns, got {len(run_calls)}"
        )

        # 8. Verify Docker params for each stage
        for i, (stage_name, kwargs) in enumerate(
            zip(orchestrator.STAGE_ORDER, run_calls)
        ):
            expected_prev = orchestrator._previous_stage_name(stage_name)

            # --- image ---
            assert kwargs.get("image") == settings.AGENT_IMAGE, (
                f"Stage {stage_name}: wrong image"
            )

            # --- detach ---
            assert kwargs.get("detach") is True, (
                f"Stage {stage_name}: detach should be True"
            )

            # --- network ---
            assert kwargs.get("network") == settings.AGENT_NETWORK, (
                f"Stage {stage_name}: wrong network"
            )

            # --- user ---
            expected_user = f":{settings.AGENT_CONTAINER_GID}"
            assert kwargs.get("user") == expected_user, (
                f"Stage {stage_name}: expected user {expected_user}, "
                f"got {kwargs.get('user')}"
            )

            # --- command ---
            command = kwargs.get("command", [])
            assert isinstance(command, list) and len(command) > 0, (
                f"Stage {stage_name}: command is empty"
            )
            cmd_str = " ".join(command)
            assert stage_name in cmd_str, (
                f"Stage {stage_name}: command does not contain stage name: {cmd_str}"
            )

            # --- environment ---
            env = kwargs.get("environment", {})
            assert env.get("STAGE") == stage_name, (
                f"Stage {stage_name}: STAGE env mismatch, got {env.get('STAGE')}"
            )
            assert env.get("PIPELINE_ID") == str(pipeline.id), (
                f"Stage {stage_name}: PIPELINE_ID env mismatch"
            )
            assert env.get("BRANCH_NAME") == pipeline.invocation_name, (
                f"Stage {stage_name}: BRANCH_NAME env mismatch"
            )
            assert env.get("HOME") == "/home/wywy", (
                f"Stage {stage_name}: HOME env mismatch"
            )
            assert env.get("PREVIOUS_STAGE") == expected_prev, (
                f"Stage {stage_name}: expected PREVIOUS_STAGE={expected_prev!r}, "
                f"got {env.get('PREVIOUS_STAGE')!r}"
            )
            for key in (
                "DEEPSEEK_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENCODE_API_KEY",
            ):
                assert key in env, (
                    f"Stage {stage_name}: missing env var {key}"
                )

            # --- volumes ---
            volumes: dict[str, dict[str, str]] = kwargs.get("volumes", {})
            copies_dir = workspace / "copies"
            for repo in orchestrator.REPO_CONFIG:
                repo_path = copies_dir / repo["mount"].lstrip("/")
                if repo_path.exists():
                    repo_key = str(repo_path)
                    assert repo_key in volumes, (
                        f"Stage {stage_name}: missing volume for repo {repo['name']}"
                    )
                    vol = volumes[repo_key]
                    assert vol.get("bind") == repo["mount"], (
                        f"Stage {stage_name}: wrong bind for {repo['name']}"
                    )
                    assert vol.get("mode") == "rw", (
                        f"Stage {stage_name}: repo {repo['name']} should be rw"
                    )

            # Shared volumes
            vol_binds = {v.get("bind"): v for v in volumes.values() if "bind" in v}
            assert "/state" in vol_binds, (
                f"Stage {stage_name}: missing /state volume"
            )
            assert vol_binds["/state"].get("mode") == "rw"
            assert "/artifacts" in vol_binds, (
                f"Stage {stage_name}: missing /artifacts volume"
            )
            assert vol_binds["/artifacts"].get("mode") == "rw"
            assert "/context" in vol_binds, (
                f"Stage {stage_name}: missing /context volume"
            )
            assert vol_binds["/context"].get("mode") == "rw"
            assert "/logs" in vol_binds, (
                f"Stage {stage_name}: missing /logs volume"
            )
            assert vol_binds["/logs"].get("mode") == "rw"
            assert "/workspace/.opencode" in vol_binds, (
                f"Stage {stage_name}: missing /workspace/.opencode volume"
            )
            assert vol_binds["/workspace/.opencode"].get("mode") == "ro", (
                f"Stage {stage_name}: /workspace/.opencode should be ro"
            )

        # 9. Verify pipeline completed
        pipeline.refresh_from_db()
        assert pipeline.status == "completed"
        for name in orchestrator.STAGE_ORDER:
            st = pipeline.stages.get(name=name)
            assert st.status == "completed"
