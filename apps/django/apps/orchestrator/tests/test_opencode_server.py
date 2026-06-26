"""Tests for the opencode server pipeline — HTTP-based orchestration.

The opencode server pipeline replaces per-stage `opencode run` CLI containers
with a single long-lived `opencode serve` HTTP container.  Each stage creates
a new opencode session and communicates via HTTP.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings
from django.test import override_settings
from django.utils import timezone as dj_timezone

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage


EXPECTED_STAGE_ORDER = [
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compliance",
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


def _mock_http(monkeypatch: MonkeyPatch) -> None:
    """Replace HTTP helpers so _run_stage reaches the polling loop."""
    monkeypatch.setattr(
        orchestrator, "_opencode_post",
        lambda p, path, **kw: {"id": "session-1"},
    )
    monkeypatch.setattr(
        orchestrator, "_opencode_get",
        lambda p, path, **kw: {},
    )
    monkeypatch.setattr(
        orchestrator, "_get_server_url",
        lambda p: "http://server:4096",
    )


def _write_stage_state(
    pipeline: Pipeline,
    stage_name: str,
    status: str,
    output: dict | None = None,
) -> None:
    """Write a stage status to state.json the same way the agent would."""
    state_path = orchestrator._state_file_path(pipeline)
    state = json.loads(state_path.read_text())
    entry: dict[str, Any] = {"status": status}
    if output is not None:
        entry["output"] = output
    state["stages"][stage_name] = entry
    state["updated_at"] = time.time()
    tmp_path = Path(str(state_path) + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2))
    tmp_path.rename(state_path)


def _setup_pipeline_and_mocks(
    db: None,
    temp_workspace: Path,
    temp_log_root: Path,
    monkeypatch: MonkeyPatch,
    stage_name: str = "RED",
) -> tuple[Pipeline, PipelineStage]:
    """Create a pipeline, workspace, and state.json with all stages
    ``"pending"``.  Marks the ``init`` stage as completed so that
    ``stage_name`` can be advanced to immediately.

    Also mocks the low-level HTTP calls so ``_run_stage_via_server``
    can execute without a real opencode server container.

    Returns (pipeline, target_stage).
    """
    pipeline = Pipeline.objects.create(
        invocation_name="setup-pipeline",
        description="Test setup",
        status="running",
    )
    orchestrator._create_stages(pipeline)

    # Create workspace structure manually — avoids real file-copy.
    workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "pipeline_id": str(pipeline.id),
        "status": "running",
        "current_stage": None,
        "stages": {
            name: {"status": "pending", "output": None}
            for name in orchestrator.STAGE_ORDER
        },
        "updated_at": "2025-01-01T00:00:00",
    }
    (state_dir / "state.json").write_text(json.dumps(state, indent=2))

    # Mark ``init`` as completed so advance_pipeline targets the
    # requested stage.
    init_stage = pipeline.stages.get(name="init")
    init_stage.status = "completed"
    init_stage.save(update_fields=["status"])
    pipeline.current_stage = "init"
    pipeline.save(update_fields=["current_stage", "updated_at"])

    target = pipeline.stages.get(name=stage_name)

    # Mock low-level HTTP calls so ``_run_stage_via_server`` can
    # execute without a real opencode server container.
    monkeypatch.setattr(
        orchestrator, "_get_server_url", lambda p: "http://server:4096",
    )
    monkeypatch.setattr(
        orchestrator, "_stop_opencode_server", lambda p: None,
    )
    monkeypatch.setattr(
        orchestrator, "_create_pr", lambda p: None,
    )
    monkeypatch.setattr(
        orchestrator, "_teardown_workspace", lambda p: None,
    )
    monkeypatch.setattr(
        orchestrator, "_run_formatters", lambda p: None,
    )

    # Shorten poll timeout so tests don't block for the production
    # default (600 s) when no agent writes to state.json.
    monkeypatch.setattr(orchestrator, "STATE_POLL_TIMEOUT", 3)

    return pipeline, target


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


    def test_agent_failed_status_triggers_retry_not_completed(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the agent writes ``status: "failed"`` directly to state.json,
        ``_run_stage`` must NOT mark the stage as ``"completed"`` — it must
        enter retry via ``_handle_stage_failure`` instead.

        The polling loop in ``_run_stage_via_server`` treats ``"failed"`` as a
        terminal status alongside ``"completed"`` and ``"blocked"``.  But
        ``_validate_stage_state`` also accepts ``"failed"`` as valid.  When
        validation passes, ``_run_stage`` unconditionally sets
        ``stage.status = "completed"`` — the agent's explicit failure signal
        is silently discarded.

        **Current behaviour (bug):** ``stage.status`` becomes ``"completed"``
        even though the agent reported ``"failed"``.

        **Required behaviour (fix):** ``_run_stage_via_server`` must return a
        non-zero exit code for ``"failed"`` so that ``_run_stage`` routes to
        ``_handle_stage_failure``.
        """
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        pipeline = Pipeline.objects.create(
            invocation_name="agent-failed-status",
            description="test agent writing failed to state.json",
            status="running",
            current_stage=None,
        )
        orchestrator._create_workspace(pipeline)
        orchestrator._create_stages(pipeline)

        stage = pipeline.stages.get(name="RED")

        # ── Simulate agent writing "failed" to state.json ───────────────
        _write_stage_state(
            pipeline,
            stage.name,
            "failed",
            output={"error": "Agent encountered an error"},
        )

        # Mock HTTP — must succeed so we reach the polling loop
        _mock_http(monkeypatch)

        # Do NOT monkeypatch _handle_stage_failure — the production code
        # must route to it naturally.

        orchestrator._run_stage(pipeline, stage)

        stage.refresh_from_db()
        pipeline.refresh_from_db()

        # ── THIS ASSERTION FAILS WITH THE CURRENT CODE ──────────────────
        # The stage is marked "completed" even though the agent reported
        # "failed".  Fix _run_stage_via_server to return exit_code=1 when
        # the agent writes "failed", so _handle_stage_failure is invoked.
        assert stage.status != "completed", (
            f"Stage must NOT be 'completed' when agent wrote 'failed'. "
            f"Got: '{stage.status}'"
        )
        assert stage.status in ("pending", "failed"), (
            f"Expected 'pending' (retry) or 'failed', got '{stage.status}'"
        )
        assert stage.retry_count >= 1, (
            f"Expected retry_count >= 1 when agent reported failure, "
            f"got {stage.retry_count}"
        )

    def test_stale_failed_status_reset_in_state_json_on_retry(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """After a stage fails (agent wrote ``"failed"`` to state.json) and
        enters retry, the orchestrator must reset the stage's status in
        state.json so that a subsequent retry does not immediately re-detect
        the stale ``"failed"`` status.

        **Current behaviour (bug):** ``_run_stage_via_server`` reads the stale
        ``"failed"`` from state.json on the first poll of every retry attempt,
        returning ``exit_code=1`` before the agent has any chance to write
        new output.  Each retry is wasted — the agent never gets to run.

        **Required behaviour (fix):** When ``_run_stage`` starts a retry
        (``retry_count > 0``), it must update state.json to reflect the new
        attempt by resetting the stage status (e.g., to ``"running"``).
        """
        pipeline, stage = TestAsyncAgentCompletionGap._setup_pipeline_for_stage(
            db, temp_workspace, temp_log_root, monkeypatch,
        )

        _mock_http(monkeypatch)

        # ── Simulate agent writing "failed" to state.json ─────────────────
        _write_stage_state(pipeline, stage.name, "failed")

        # ── First run: agent-reported failure → retry ────────────────────
        orchestrator._run_stage(pipeline, stage)
        stage.refresh_from_db()
        assert stage.retry_count == 1, (
            f"Expected retry after first attempt, "
            f"got retry_count={stage.retry_count}"
        )
        assert stage.status in ("pending", "failed")

        # ── Second run (retry): state.json should not have stale "failed" ─
        orchestrator._run_stage(pipeline, stage)

        # ── THIS ASSERTION FAILS WITH THE CURRENT CODE ───────────────────
        # State.json still has "failed" from the first run.  The fix must
        # reset it so the poll loop doesn't immediately re-detect it.
        state_path = orchestrator._state_file_path(pipeline)
        state = json.loads(state_path.read_text())
        stage_state = state.get("stages", {}).get(stage.name, {})
        assert stage_state.get("status") != "failed", (
            f"Stage status in state.json must NOT be 'failed' after a retry "
            f"starts.  Got: '{stage_state.get('status')}'.  The orchestrator "
            f"must reset the status (e.g. to 'running') so that a fresh poll "
            f"does not immediately re-detect the stale failure."
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


# ── RED: async agent communication gap ─────────────────────────────────

class TestAsyncAgentCompletionGap:
    """Tests that expose the gap between sending a prompt to the agent and
    waiting for it to write results to state.json.

    ``_run_stage_via_server`` sends a message to the agent and returns
    immediately without polling state.json for the agent's response.
    This means ``_validate_stage_state`` always fails on the first attempt
    because the agent hasn't had time to write ``"completed"``.

    Test 1 (``test_validation_fails_when_agent_not_done``) documents the
    current behaviour — it *passes* because the bug is present.

    Test 2 (``test_run_stage_via_server_must_poll_until_completed``) defines
    the required fix — it *fails* because ``_run_stage_via_server`` returns
    before the (simulated) agent has written to state.json.
    """

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _setup_pipeline_for_stage(
        db: None,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        stage_name: str = "RED",
    ) -> tuple[Pipeline, PipelineStage]:
        """Create a pipeline, workspace, and state.json with all stages
        ``"pending"``.  Marks the ``init`` stage as completed so that
        ``stage_name`` can be advanced to immediately.

        Delegates to the module-level :func:`_setup_pipeline_and_mocks`.
        """
        return _setup_pipeline_and_mocks(
            db, temp_workspace, temp_log_root, monkeypatch, stage_name,
        )

    # ── Test 1: document the bug (passes now) ───────────────────────────

    def test_validation_fails_when_agent_not_done(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the agent has *not* yet written ``"completed"`` to
        state.json, ``_validate_stage_state`` returns ``False`` and the
        stage enters retry mode.

        This test **passes** with the current code — it documents the
        buggy behaviour that needs to change.
        """
        pipeline, stage = self._setup_pipeline_for_stage(
            db, temp_workspace, temp_log_root, monkeypatch,
        )

        # Mock HTTP: simulate successful message delivery BUT the agent
        # does NOT write to state.json (agent is still "thinking").
        monkeypatch.setattr(
            orchestrator, "_opencode_post",
            lambda p, path, **kw: {"id": "session-1"},
        )
        monkeypatch.setattr(
            orchestrator, "_opencode_get",
            lambda p, path, **kw: {},
        )

        # Very important: do NOT mock ``_validate_stage_state`` — we want
        # the real validation to run against the unchanged state.json.
        # Do NOT mock ``_spawn_agent_container`` either — we want the
        # real ``_run_stage_via_server`` code path.

        # Act: run the stage
        orchestrator._run_stage(pipeline, stage)

        # Assert: stage went into retry (not completed!)
        stage.refresh_from_db()
        pipeline.refresh_from_db()

        assert stage.status != "completed", (
            "Stage must NOT be completed — agent hasn't written to state.json"
        )
        assert stage.status == "pending", (
            f"Expected 'pending' (retry), got '{stage.status}'"
        )
        assert stage.retry_count == 1, (
            f"Expected retry_count=1, got {stage.retry_count}"
        )
        assert stage.retry_after is not None, (
            "retry_after must be set for backoff when agent didn't respond"
        )
        assert pipeline.status == "running", (
            f"Pipeline should still be running, got '{pipeline.status}'"
        )

        # Verify ``_validate_stage_state`` was the reason validation failed
        # by checking state.json still has 'pending' status.
        state_path = orchestrator._state_file_path(pipeline)
        state = json.loads(state_path.read_text())
        stage_state = state.get("stages", {}).get(stage.name, {})
        assert stage_state.get("status") == "pending", (
            f"state.json still has status='{stage_state.get('status')}' "
            f"(expected 'pending' since agent never wrote)"
        )

    # ── Test 2: define the fix (FAILS now) ──────────────────────────────

    def test_run_stage_via_server_must_poll_until_completed(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """``_run_stage_via_server`` must poll state.json until the agent
        writes ``"completed"`` (or ``"blocked"``), rather than returning
        immediately after sending the prompt.

        The test simulates an agent that takes ~500 ms to respond:
        a background thread writes ``"completed"`` to state.json after a
        short delay.

        **Current behaviour (bug):** ``_run_stage_via_server`` returns
        before the agent has written to state.json, so validation fails
        and the stage enters retry mode.

        **Required behaviour (fix):** ``_run_stage_via_server`` loops
        (polls state.json + sleeps) until the status transitions to
        ``"completed"``, ``"blocked"``, or a timeout is reached.
        """
        pipeline, stage = self._setup_pipeline_for_stage(
            db, temp_workspace, temp_log_root, monkeypatch,
        )

        # Mock HTTP calls — simulate a successful session.
        monkeypatch.setattr(
            orchestrator, "_opencode_post",
            lambda p, path, **kw: {"id": "session-1"},
        )
        monkeypatch.setattr(
            orchestrator, "_opencode_get",
            lambda p, path, **kw: {},
        )

        # ── Background thread: simulate the agent writing state.json ──
        state_path = orchestrator._state_file_path(pipeline)
        agent_done = threading.Event()

        def agent_writes_completed() -> None:
            """Wait a moment (simulating agent processing), then write
            ``"completed"`` to state.json for the current stage."""
            time.sleep(0.3)
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return
            state.setdefault("stages", {})[stage.name] = {
                "status": "completed",
                "output": {"message": f"{stage.name} completed"},
            }
            state["updated_at"] = time.time()
            tmp = Path(str(state_path) + ".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.rename(state_path)
            agent_done.set()

        t = threading.Thread(target=agent_writes_completed, daemon=True)
        t.start()

        # ── Act: run the stage ──────────────────────────────────────────
        # ``_run_stage`` calls ``_spawn_agent_container`` →
        # ``_run_stage_via_server``.
        #
        # With the fix, ``_run_stage_via_server`` will poll state.json,
        # discover that the agent wrote ``"completed"``, and return.
        # ``_validate_stage_state`` then passes and the stage finishes.
        orchestrator._run_stage(pipeline, stage)

        # Wait for the background thread to finish (so we can inspect
        # its side effects regardless of test outcome).
        agent_done.wait(timeout=5)

        stage.refresh_from_db()
        pipeline.refresh_from_db()

        # ── THIS ASSERTION FAILS WITH THE CURRENT CODE ──────────────────
        # ``_run_stage_via_server`` doesn't poll — it returns immediately
        # and the agent hasn't written to state.json yet, so validation
        # fails and the stage goes into retry.
        assert stage.status == "completed", (
            f"_run_stage_via_server must poll state.json until the agent "
            f"responds.  Expected 'completed', got '{stage.status}'.  "
            f"retry_count={stage.retry_count}, "
            f"retry_after={stage.retry_after}"
        )

        # Sanity check: the agent DID write to state.json (eventually).
        state = json.loads(state_path.read_text())
        stage_state = state.get("stages", {}).get(stage.name, {})
        assert stage_state.get("status") == "completed", (
            "Agent should have written 'completed' to state.json"
        )

        assert pipeline.status == "running", (
            f"Pipeline should still be running, got '{pipeline.status}'"
        )

    # ── Test 3: downstream effect (FAILS now) ──────────────────────────

    def test_out_of_band_state_write_not_detected_without_polling(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Even when the agent writes ``"completed"`` to state.json
        *between* orchestrator loop ticks, the pipeline fails to make
        progress because ``_run_stage_via_server`` already returned and
        the retry guard blocks re-entry.

        This test simulates the following sequence:

        1. ``advance_pipeline`` → ``_run_stage`` → ``_run_stage_via_server``
           sends prompt, returns immediately.
        2. ``_validate_stage_state`` fails → ``_handle_stage_failure`` →
           stage set to ``"pending"`` with ``retry_after``.
        3. Agent writes ``"completed"`` to state.json (but too late).
        4. ``advance_pipeline`` called again — retry guard blocks it
           because ``retry_after > now``.

        **Current behaviour (bug):** The stage never completes even though
        the agent did its work.

        **Required behaviour (fix):** ``_run_stage_via_server`` polls
        state.json during step 1, discovers the agent's write (step 3
        overlaps with step 1), and returns ``"completed"``.
        """
        pipeline, stage = self._setup_pipeline_for_stage(
            db, temp_workspace, temp_log_root, monkeypatch,
        )

        monkeypatch.setattr(
            orchestrator, "_opencode_post",
            lambda p, path, **kw: {"id": "session-1"},
        )
        monkeypatch.setattr(
            orchestrator, "_opencode_get",
            lambda p, path, **kw: {},
        )

        state_path = orchestrator._state_file_path(pipeline)

        # ── Step 1: first advance (prompt sent, agent hasn't responded) ─
        orchestrator.advance_pipeline(pipeline)

        stage.refresh_from_db()
        pipeline.refresh_from_db()

        # Verify stage entered retry (this is the bug symptom)
        assert stage.status == "pending", (
            f"Expected 'pending' (retry) after first advance, "
            f"got '{stage.status}'"
        )
        assert stage.retry_after is not None
        first_retry_after = stage.retry_after

        # ── Step 2: agent writes 'completed' to state.json ──────────────
        state = json.loads(state_path.read_text())
        state["stages"][stage.name] = {
            "status": "completed",
            "output": {"message": f"{stage.name} completed"},
        }
        state["updated_at"] = time.time()
        tmp = Path(str(state_path) + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(state_path)

        # ── Step 3: second advance (should detect completion) ───────────
        # With the current code this is blocked by retry_after.
        # With the fix, _run_stage_via_server would have already polled
        # and completed the stage in step 1 — this test documents the
        # downstream consequence of *not* polling.
        orchestrator.advance_pipeline(pipeline)

        stage.refresh_from_db()

        # ── THIS ASSERTION FAILS WITH THE CURRENT CODE ──────────────────
        assert stage.status == "completed", (
            f"Stage should be 'completed' because the agent wrote "
            f"'completed' to state.json before the second advance.  "
            f"Got '{stage.status}' instead — retry_after={stage.retry_after} "
            f"blocks re-entry because _run_stage_via_server didn't poll "
            f"during the first advance."
        )

        # Compare retry_after from the first failure — if unchanged the
        # second advance was indeed a no-op.
        stage.refresh_from_db()
        if stage.retry_after == first_retry_after:
            pytest.fail(
                "Second advance was a no-op — blocked by retry_after guard. "
                "The agent wrote 'completed' to state.json between ticks "
                "but the orchestrator never re-validated."
            )


class TestStagePromptUsesWorkspaceStateJson:
    """The stage prompt sent to the opencode server must reference
    ``/workspace/state/state.json`` so the ``read`` tool can access the
    state file from within the workspace."""

    def test_prompt_uses_workspace_state_json_path(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Sending a stage prompt with ``/state/state.json`` causes the
        opencode ``read`` tool to hang because the path is outside the
        allowed workspace directory.  Every stage must instruct the agent
        to use ``/workspace/state/state.json`` instead."""
        pipeline, target = _setup_pipeline_and_mocks(
            db, temp_workspace, temp_log_root, monkeypatch, stage_name="RED",
        )

        # ── Capture HTTP POST payloads ──────────────────────────────
        http_posts: list[dict[str, Any]] = []

        def mock_post(
            pipeline: Pipeline, path: str, **kw: Any,
        ) -> dict[str, Any]:
            http_posts.append({"path": path, "kwargs": kw})
            return {"id": f"session-{len(http_posts)}"}

        def mock_get(
            pipeline: Pipeline, path: str, **kw: Any,
        ) -> dict[str, Any]:
            return {}

        monkeypatch.setattr(orchestrator, "_opencode_post", mock_post)
        monkeypatch.setattr(orchestrator, "_opencode_get", mock_get)

        # ── Act ─────────────────────────────────────────────────────
        orchestrator._run_stage(pipeline, target)

        # ── Assert ──────────────────────────────────────────────────
        message_posts = [p for p in http_posts if "/message" in p["path"]]
        assert len(message_posts) >= 1, (
            "Expected at least one message POST to the opencode server"
        )

        parts = message_posts[0]["kwargs"].get("json", {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts)
        assert "/workspace/state/state.json" in text, (
            f"Stage prompt must reference /workspace/state/state.json "
            f"so the opencode read tool can access the state file.\n"
            f"Got: {text}"
        )
