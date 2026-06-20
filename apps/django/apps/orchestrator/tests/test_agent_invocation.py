"""Parameterized test: verify agent invocation at every pipeline stage.

One basic pattern driven by a constant table — for each of the 6 stages,
pre-complete prior stages, mock _spawn_agent_container, run the pipeline
to completion, and verify the correct stage was spawned.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage

STAGE_INVOCATION_PARAMS = [
    ("init",         "",            []),
    ("RED",          "init",        ["init"]),
    ("GREEN",        "RED",         ["init", "RED"]),
    ("REFRACTOR",    "GREEN",       ["init", "RED", "GREEN"]),
    ("compilance",   "REFRACTOR",   ["init", "RED", "GREEN", "REFRACTOR"]),
    ("PR writer",   "compilance",  ["init", "RED", "GREEN", "REFRACTOR", "compilance"]),
]

EXPECTED_STAGE_ORDER = [
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compilance",
    "PR writer",
]


def _pre_write_all_stages_completed(pipeline: Pipeline) -> None:
    state_path = orchestrator._state_file_path(pipeline)
    state = json.loads(state_path.read_text())
    for name in orchestrator.STAGE_ORDER:
        state.setdefault("stages", {})[name] = {
            "status": "completed",
        }
    state["updated_at"] = time.time()
    tmp = Path(str(state_path) + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(state_path)


class TestAgentInvocationPerStage:

    @pytest.mark.parametrize("stage_name,expected_prev,prior_stages", STAGE_INVOCATION_PARAMS)
    def test_agent_invoked_for_stage(
        self,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        stage_name: str,
        expected_prev: str,
        prior_stages: list[str],
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        # 1. Create pipeline via ORM
        pipeline = Pipeline.objects.create(
            invocation_name=f"agent-test-{stage_name}",
            description=f"Agent invocation test for {stage_name}",
            status="queued",
        )

        # 2. Bootstrap pipeline
        orchestrator._create_workspace(pipeline)
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._create_stages(pipeline)
        pipeline.refresh_from_db()

        # 3. Pre-complete prior stages in DB
        for name in prior_stages:
            st = pipeline.stages.get(name=name)
            st.status = "completed"
            st.save(update_fields=["status"])
        last_prior = prior_stages[-1] if prior_stages else None
        pipeline.current_stage = last_prior
        pipeline.save(update_fields=["current_stage", "updated_at"])

        # 4. Pre-write state.json so _validate_stage_state passes for all stages
        _pre_write_all_stages_completed(pipeline)

        # 5. Mock _spawn_agent_container to capture calls
        spawn_calls: list[tuple[Pipeline, PipelineStage]] = []

        def mock_spawn(p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append((p, s))
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)

        # 6. Mock external side-effects
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # 7. Run — advance_pipeline should advance exactly one stage (iterative)
        orchestrator.advance_pipeline(pipeline)

        # 8. Verify target stage was spawned
        assert len(spawn_calls) > 0, f"No agent spawned for stage {stage_name}"
        spawned_pipeline, spawned_stage = spawn_calls[0]
        assert spawned_stage.name == stage_name, (
            f"Expected spawn to be {stage_name}, got {spawned_stage.name}"
        )

        # 9. Verify call context
        assert spawned_pipeline.id == pipeline.id
        assert spawned_pipeline.invocation_name == f"agent-test-{stage_name}"
        assert spawned_stage.name == stage_name

        # 10. Verify only one stage was spawned (iterative — not recursive)
        assert len(spawn_calls) == 1, (
            f"Expected exactly 1 spawn (iterative), got {len(spawn_calls)}: "
            f"{[s.name for _, s in spawn_calls]}"
        )

        # 11. Verify pipeline is still running and only the target stage completed
        pipeline.refresh_from_db()
        assert pipeline.status == "running", (
            f"Pipeline should still be running after one advance, "
            f"got {pipeline.status}"
        )
        target = pipeline.stages.get(name=stage_name)
        expected = "pending" if stage_name == "init" else "completed"
        assert target.status == expected, (
            f"Stage {stage_name} should be {expected}, got {target.status}"
        )
        for name in orchestrator.STAGE_ORDER:
            if name == stage_name:
                continue
            st = pipeline.stages.get(name=name)
            if name in prior_stages:
                assert st.status == "completed", (
                    f"Prior stage {name} should be completed, got {st.status}"
                )
            else:
                assert st.status in ("pending", "running"), (
                    f"Future stage {name} should be pending, got {st.status}"
                )


class TestInitStageInitialization:
    """The init stage must perform pipeline initialization
    (_create_workspace, _start_opencode_server, _wait_for_server_health)
    rather than only spawning an agent container."""

    def test_init_stage_performs_workspace_creation(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The init stage must call _create_workspace, _start_opencode_server,
        and _wait_for_server_health before spawning the agent container."""
        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="test-init-does-setup",
            description="Verify init stage performs setup",
            status="queued",
        )

        # Create stages first so advance_pipeline can find init
        orchestrator._create_stages(pipeline)
        pipeline.status = "running"
        pipeline.save(update_fields=["status"])

        # Record calls to initialization functions
        init_calls: list[str] = []

        def record_create_workspace(p: Pipeline) -> None:
            init_calls.append("_create_workspace")

        def record_start_server(p: Pipeline) -> None:
            init_calls.append("_start_opencode_server")

        def record_wait_health(p: Pipeline) -> None:
            init_calls.append("_wait_for_server_health")

        monkeypatch.setattr(
            orchestrator, "_create_workspace", record_create_workspace,
        )
        monkeypatch.setattr(
            orchestrator, "_start_opencode_server", record_start_server,
        )
        monkeypatch.setattr(
            orchestrator, "_wait_for_server_health", record_wait_health,
        )

        # Mock agent spawn to succeed immediately
        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container",
            lambda p, s: (0, False),
        )

        # Mock _validate_stage_state — the mock agent won't write to
        # state.json, so the real validation would fail.
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state",
            lambda p, s: (True, ""),
        )

        # Mock other external side-effects
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # ── Act ──────────────────────────────────────────────────────────
        # advance_pipeline should target init (current_stage is None)
        orchestrator.advance_pipeline(pipeline)

        # ── Assert ───────────────────────────────────────────────────────
        assert "_create_workspace" in init_calls, (
            "Init stage must call _create_workspace"
        )
        assert "_start_opencode_server" in init_calls, (
            "Init stage must call _start_opencode_server"
        )
        assert "_wait_for_server_health" in init_calls, (
            "Init stage must call _wait_for_server_health"
        )
        # Verify order: workspace creation before server start
        create_idx = init_calls.index("_create_workspace")
        server_idx = init_calls.index("_start_opencode_server")
        health_idx = init_calls.index("_wait_for_server_health")
        assert create_idx < server_idx, (
            "_create_workspace must be called before _start_opencode_server"
        )
        assert server_idx < health_idx, (
            "_start_opencode_server must be called before _wait_for_server_health"
        )

    def test_init_stage_fails_pipeline_when_workspace_creation_fails(
        self,
        db,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """If init stage's _create_workspace raises OSError, the pipeline
        must transition to 'failed'."""
        pipeline = Pipeline.objects.create(
            invocation_name="test-init-failure",
            description="Verify init failure handling",
            status="queued",
        )
        orchestrator._create_stages(pipeline)
        pipeline.status = "running"
        pipeline.save(update_fields=["status"])

        # Make _create_workspace fail with OSError
        monkeypatch.setattr(
            orchestrator, "_create_workspace",
            lambda p: (_ for _ in ()).throw(OSError("disk full")),
        )

        # Mock server startup to fail if called (shouldn't be reached)
        monkeypatch.setattr(
            orchestrator, "_start_opencode_server",
            lambda p: pytest.fail("_start_opencode_server should not be called"),
        )
        monkeypatch.setattr(
            orchestrator, "_wait_for_server_health",
            lambda p: pytest.fail("_wait_for_server_health should not be called"),
        )

        # Mock other side-effects
        monkeypatch.setattr(orchestrator, "_spawn_agent_container",
                            lambda p, s: pytest.fail("_spawn_agent_container should not be called"))
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator.advance_pipeline(pipeline)

        # ── Assert ───────────────────────────────────────────────────────
        pipeline.refresh_from_db()
        assert pipeline.status == "failed", (
            f"Pipeline should be failed when init setup fails, "
            f"got {pipeline.status}"
        )
        init_stage = pipeline.stages.get(name="init")
        assert init_stage.status in ("pending", "failed"), (
            f"Init stage should not have completed, got {init_stage.status}"
        )
