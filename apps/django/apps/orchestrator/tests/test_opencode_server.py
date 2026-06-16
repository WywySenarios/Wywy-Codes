"""Tests for the opencode server pipeline — HTTP-based orchestration.

The opencode server pipeline replaces per-stage `opencode run` CLI containers
with a single long-lived `opencode serve` HTTP container.  Each stage creates
a new opencode session and communicates via HTTP.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings
from django.test import override_settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage


EXPECTED_STAGE_ORDER = [
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compilance",
    "PR writer",
]


# ── helpers ────────────────────────────────────────────────────────────────

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


# ── RED: _run_stage must use HTTP, not Docker spawns ───────────────────────

class TestRunStageUsesServerNotDocker:
    """When advance_pipeline runs through all 9 stages, _spawn_agent_container
    is never called.  Instead, each stage creates an opencode session and
    sends a prompt via HTTP."""

    def test_stages_use_http_not_docker_spawn(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        pipeline = Pipeline.objects.create(
            invocation_name="server-integration",
            description="test",
            status="queued",
        )
        orchestrator._create_workspace(pipeline)
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._create_stages(pipeline)

        # Pre-write state so _validate_stage_state passes for all stages
        state_path = orchestrator._state_file_path(pipeline)
        state = json.loads(state_path.read_text())
        for name in orchestrator.STAGE_ORDER:
            state.setdefault("stages", {})[name] = {"status": "completed"}
        state["updated_at"] = time.time()
        tmp = Path(str(state_path) + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(state_path)

        # Track HTTP calls
        http_posts: list[dict[str, Any]] = []

        def mock_post(pipeline: Pipeline, path: str, **kwargs) -> dict:
            http_posts.append({"pipeline_id": str(pipeline.id), "path": path, "kwargs": kwargs})
            return {"id": f"session-{len(http_posts)}"}

        def mock_get(pipeline: Pipeline, path: str, **kwargs) -> dict:
            return {}

        monkeypatch.setattr(orchestrator, "_opencode_post", mock_post)
        monkeypatch.setattr(orchestrator, "_opencode_get", mock_get)
        monkeypatch.setattr(orchestrator, "_get_server_url",
                           lambda p: "http://server:4096")
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        orchestrator.advance_pipeline(pipeline)

        pipeline.refresh_from_db()
        # Iterative: only one stage runs per call, pipeline stays running
        assert pipeline.status == "running", (
            f"Pipeline should still be running (iterative), got {pipeline.status}"
        )

        # Each stage = 2 HTTP POSTs (create session + send message).
        # Iterative: only the first stage should have been invoked.
        assert len(http_posts) == 2, (
            f"Expected 2 HTTP POSTs for one stage (iterative), "
            f"got {len(http_posts)}"
        )

        # Verify the first stage created a session
        session_paths: list[str] = []
        message_paths: list[str] = []
        for post in http_posts:
            if "/message" in post["path"]:
                message_paths.append(post["path"])
            else:
                session_paths.append(post["path"])

        # Iterative: only the first stage ran — 1 session + 1 message
        assert len(session_paths) == 1, (
            f"Expected 1 session for the first stage, "
            f"got {len(session_paths)}"
        )
        assert len(message_paths) == 1, (
            f"Expected 1 message for the first stage, "
            f"got {len(message_paths)}"
        )

        # The session title must match the first (and only) stage name
        first_session = http_posts[0]
        assert first_session["kwargs"]["json"]["title"] == orchestrator.STAGE_ORDER[0], (
            f"Session title should be '{orchestrator.STAGE_ORDER[0]}', "
            f"got '{first_session['kwargs']['json'].get('title')}'"
        )

    def test_blocked_stage_stops_pipeline(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When _check_blocked_state returns True, the pipeline stops advancing
        and awaits user input."""
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        pipeline = Pipeline.objects.create(
            invocation_name="blocked-integration",
            description="test",
            status="running",
            current_stage=None,
        )
        orchestrator._create_workspace(pipeline)
        orchestrator._create_stages(pipeline)

        stage = pipeline.stages.get(name="RED")

        def mock_post(pipeline: Pipeline, path: str, **kwargs) -> dict:
            return {"id": "session-1"}

        def mock_get(pipeline: Pipeline, path: str, **kwargs) -> dict:
            return {}

        def mock_check(pipeline, stage):
            pipeline.user_input_request = {"question": "Which approach?"}
            pipeline.save(update_fields=["user_input_request"])
            return True

        monkeypatch.setattr(orchestrator, "_opencode_post", mock_post)
        monkeypatch.setattr(orchestrator, "_opencode_get", mock_get)
        monkeypatch.setattr(orchestrator, "_get_server_url",
                           lambda p: "http://server:4096")
        monkeypatch.setattr(orchestrator, "_check_blocked_state", mock_check)

        orchestrator._run_stage(pipeline, stage)

        pipeline.refresh_from_db()
        stage.refresh_from_db()
        assert stage.status == "blocked"
        assert pipeline.user_input_pending is True

    def test_http_failure_triggers_retry(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the HTTP call fails (OSError), _handle_stage_failure is invoked."""
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        pipeline = Pipeline.objects.create(
            invocation_name="failure-integration",
            description="test",
            status="running",
            current_stage=None,
        )
        orchestrator._create_workspace(pipeline)
        orchestrator._create_stages(pipeline)

        stage = pipeline.stages.get(name="RED")

        posts_called: list[str] = []

        def mock_post(pipeline: Pipeline, path: str, **kwargs) -> dict:
            posts_called.append(path)
            raise OSError("Connection refused")

        fail_calls: list[tuple] = []

        def mock_fail(p: Pipeline, s: PipelineStage) -> None:
            fail_calls.append((p.id, s.name))

        monkeypatch.setattr(orchestrator, "_opencode_post", mock_post)
        monkeypatch.setattr(orchestrator, "_get_server_url",
                           lambda p: "http://server:4096")
        monkeypatch.setattr(orchestrator, "_handle_stage_failure", mock_fail)

        orchestrator._run_stage(pipeline, stage)

        assert len(posts_called) > 0, (
            "Expected _opencode_post to be called in server mode"
        )
        assert len(fail_calls) == 1, (
            f"Expected _handle_stage_failure after HTTP error, "
            f"got {len(fail_calls)} calls"
        )


# ── RED: server container lifecycle ────────────────────────────────────────

class TestOpencodeServerLifecycle:
    """One `opencode serve` container per pipeline, started on exec,
    stopped on teardown."""

    def test_server_started_with_serve_command(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        pipeline = Pipeline.objects.create(
            invocation_name="start-cmd-test", description="test", status="queued",
        )
        orchestrator._create_workspace(pipeline)

        mock_containers = MockContainers()
        mock_client = type("MockClient", (), {"containers": mock_containers})()
        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)

        orchestrator._start_opencode_server(pipeline)

        cmd = mock_containers.run_calls[0].get("command", [])
        assert cmd[0] == "opencode"
        assert cmd[1] == "serve"
        assert "--port" in cmd
        assert "--hostname" in cmd
        assert mock_containers.run_calls[0].get("detach") is True

    def test_execute_pipeline_starts_and_checks_server(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        pipeline = Pipeline.objects.create(
            invocation_name="exec-start-test", description="test", status="running",
        )
        start_calls: list[Pipeline] = []
        health_calls: list[Pipeline] = []

        monkeypatch.setattr(orchestrator, "_start_opencode_server",
                           lambda p: start_calls.append(p))
        monkeypatch.setattr(orchestrator, "_stop_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health",
                           lambda p: health_calls.append(p))

        orchestrator._execute_pipeline(pipeline)

        assert len(start_calls) == 1
        assert start_calls[0].id == pipeline.id
        assert len(health_calls) == 1

    def test_teardown_and_complete_stop_server(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        stop_calls: list[Pipeline] = []

        monkeypatch.setattr(orchestrator, "_stop_opencode_server",
                           lambda p: stop_calls.append(p))

        # _teardown_workspace stops the server
        pipeline = Pipeline.objects.create(
            invocation_name="stop-test-a", description="test", status="completed",
        )
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        workspace.mkdir(parents=True, exist_ok=True)
        orchestrator._teardown_workspace(pipeline)
        assert len(stop_calls) == 1
        assert stop_calls[0].id == pipeline.id

        # _complete_pipeline also stops the server (before teardown)
        stop_calls.clear()
        pipeline2 = Pipeline.objects.create(
            invocation_name="stop-test-b", description="test", status="running",
        )
        for name in orchestrator.STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline2, name=name, status="completed")
        pipeline2.current_stage = "PR writer"
        pipeline2.save(update_fields=["current_stage"])
        workspace2 = Path(settings.WORKSPACE_ROOT) / str(pipeline2.id)
        workspace2.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)

        orchestrator._complete_pipeline(pipeline2)
        assert len(stop_calls) == 1
        assert stop_calls[0].id == pipeline2.id

    def test_server_health_polls_until_ready(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        pipeline = Pipeline.objects.create(
            invocation_name="health-test", description="test", status="queued",
        )
        attempts: list[str] = []

        def mock_get(pipeline: Pipeline, path: str, timeout: int = 10) -> dict:
            attempts.append(path)
            if len(attempts) < 3:
                raise OSError("refused")
            return {"healthy": True}

        monkeypatch.setattr(orchestrator, "_opencode_get", mock_get)
        monkeypatch.setattr(orchestrator, "_get_server_url",
                           lambda p: "http://s:4096")

        orchestrator._wait_for_server_health(pipeline)
        assert len(attempts) == 3
        assert "/global/health" in attempts[0]

    def test_server_health_raises_on_exhaustion(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        with override_settings(OPENCODE_SERVER_HEALTH_RETRIES=3, OPENCODE_SERVER_HEALTH_INTERVAL=0.01):
            pipeline = Pipeline.objects.create(
                invocation_name="health-fail", description="test", status="queued",
            )
            monkeypatch.setattr(orchestrator, "_opencode_get",
                            lambda p, path, timeout=10: (_ for _ in ()).throw(OSError("refused")))
            monkeypatch.setattr(orchestrator, "_get_server_url",
                            lambda p: "http://s:4096")

            with pytest.raises(RuntimeError, match="health"):
                orchestrator._wait_for_server_health(pipeline)
