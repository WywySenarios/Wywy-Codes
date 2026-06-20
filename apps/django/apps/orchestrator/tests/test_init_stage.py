"""Tests for the pipeline initialization stage.

The init stage is the first stage in every pipeline. It handles workspace
creation, source tree copying, and opencode server startup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage


class TestInitStageCreation:
    """The init stage must exist as a first-class stage in the pipeline."""

    def test_create_stages_creates_init_stage(
        self, pipeline_queued: Pipeline, db: None
    ) -> None:
        """``_create_stages`` should create an ``'init'`` PipelineStage row.

        ``_create_stages`` iterates ``STAGE_ORDER`` and creates a
        ``PipelineStage`` for each entry, including ``"init"``.
        """
        orchestrator._create_stages(pipeline_queued)

        stage = pipeline_queued.stages.filter(name="init").first()
        assert stage is not None, (
            "Expected an 'init' stage to exist after _create_stages, "
            "but none was found. The stage must be the first entry "
            "in orchestrator.STAGE_ORDER."
        )
        assert stage.status == "pending", (
            f"Expected init stage status to be 'pending', got '{stage.status}'"
        )


class TestInitStageFailureResilience:
    """When the init stage fails early, the pipeline must kill itself
    and NOT continue to _spawn_agent_container.

    The ``_run_stage`` init workspace-creation guard
    (``orchestrator.py:332-348``) currently catches only ``OSError``.
    Non-OSError exceptions (``shutil.Error``, ``RuntimeError``, …)
    propagate out of ``advance_pipeline`` → the pipeline stays
    ``'running'`` with the init stage stuck at ``'running'`` → a
    zombie pipeline the orchestrator loop can never advance.
    """

    def test_init_workspace_non_oserror_fails_pipeline(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When _create_workspace raises a non-OSError during the init
        stage via advance_pipeline → _run_stage, the exception must be
        caught, the pipeline/stage failed, logged at CRITICAL level,
        and _spawn_agent_container must NOT be called.

        ``shutil.Error`` (raised by ``shutil.copytree`` when source
        files vanish mid-copy or symlinks dangle) inherits from
        ``Exception`` directly, NOT from ``OSError``.  The
        ``_run_stage`` init path at ``orchestrator.py:337`` catches
        ``OSError`` only, so a non-OSError exception escapes — leaving
        the pipeline in ``'running'`` state with the init stage stuck
        at ``'running'``.  The orchestrator loop will retry every tick
        but the stage guard (line 275) permanently blocks advancement:
        the pipeline becomes a zombie.
        """
        from apps.orchestrator.orchestrator import advance_pipeline

        log_entries: list[tuple[str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: Any,
        ) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        # ── Set up pipeline ready for advance_pipeline to dispatch
        #    to _run_stage for the "init" stage.

        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        PipelineStage.objects.create(
            pipeline=pipeline_queued,
            name="init",
            status="pending",
        )

        # No state file → guard at orchestrator.py:332 passes
        state_path = (
            Path(settings.WORKSPACE_ROOT)
            / str(pipeline_queued.id)
            / "state" / "state.json"
        )
        assert not state_path.exists(), (
            "Precondition: state file must not exist so _run_stage's "
            "init workspace-creation guard is triggered"
        )

        # ── Track whether _spawn_agent_container is ever called
        spawn_calls: list[str] = []
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._spawn_agent_container",
            lambda p, s: spawn_calls.append(s.name) or (0, False),
        )

        # ── Make _create_workspace raise a non-OSError exception,
        #    exactly like shutil.Error would.

        def _failing_create_workspace(pipeline: Pipeline) -> None:
            raise RuntimeError(
                "Non-OSError during init stage workspace creation"
            )

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._create_workspace",
            _failing_create_workspace,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        # This call MUST NOT propagate an exception.  If it does,
        # the test fails because advance_pipeline didn't catch the
        # non-OSError → RED test proves the gap in OSError-only
        # except.
        advance_pipeline(pipeline_queued)

        # ── Assert pipeline and stage are properly failed.

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed", (
            "Pipeline must be set to 'failed' when _create_workspace "
            f"raises a non-OSError during init stage. "
            f"Got: {pipeline_queued.status}"
        )

        init_stage = PipelineStage.objects.get(
            pipeline=pipeline_queued, name="init",
        )
        assert init_stage.status == "failed", (
            "Init stage must be set to 'failed'. "
            f"Got: {init_stage.status}"
        )

        # ── Assert _spawn_agent_container was NEVER called.
        assert spawn_calls == [], (
            "_spawn_agent_container must NOT be called when init "
            f"workspace creation fails. Got calls: {spawn_calls}"
        )

        # ── Assert exactly ONE CRITICAL log entry — the pipeline
        #    must stop at the first failure and not produce additional
        #    ERROR/CRITICAL entries for the same incident.
        assert len(log_entries) >= 2, (
            "Expected at least two orchestrator log entries "
            f"(CRITICAL + Pipeline ended). Got: {len(log_entries)}"
        )
        level, msg = log_entries[-2]
        assert level == "CRITICAL", (
            f"Terminal init-stage failures must log at CRITICAL level "
            f"so operators can distinguish them from non-terminal "
            f"errors. Got: {level}"
        )
        assert "Non-OSError" in msg, (
            f"Log message must describe the failure. Got: {msg}"
        )
        # Ensure no double-failure: there must be exactly one CRITICAL.
        criticals = [lvl for lvl, _ in log_entries if lvl == "CRITICAL"]
        assert len(criticals) == 1, (
            f"Expected exactly 1 CRITICAL log entry for this pipeline "
            f"failure. Got {len(criticals)}: {criticals}"
        )
        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )


class TestInitStageApiErrorFeedback:
    """When a pipeline fails during init due to a missing stage row,
    the API must expose the reason so the frontend can show it.
    """

    def test_detail_api_includes_failure_reason(
        self, client, db, monkeypatch,
    ) -> None:
        """RED: When advance_pipeline fails a pipeline because the 'init'
        stage row is missing, the API detail response MUST include an
        ``error_message`` field explaining why.

        Currently the API returns ``{status: "failed", stages: [], ...}``
        with zero explanation — the frontend can't tell the user what
        went wrong.
        """
        # ── Set up the exact bug state ──
        pipeline = Pipeline.objects.create(
            invocation_name="missing-error-feedback",
            status="running",
        )
        pipeline.current_stage = None
        pipeline.save(update_fields=["current_stage"])

        monkeypatch.setattr(
            orchestrator, "_teardown_workspace", lambda p: None,
        )

        # ── Trigger the failure via the same code path ──
        orchestrator.advance_pipeline(pipeline)
        pipeline.refresh_from_db()
        assert pipeline.status == "failed", "Precondition"

        # ── Call the detail endpoint (as the frontend would) ──
        response = client.get(f"/api/pipelines/{pipeline.id}/")
        assert response.status_code == 200
        data = response.json()

        # ── RED assertion: the frontend MUST see WHY it failed ──
        assert "error_message" in data, (
            "The API response MUST include an 'error_message' field "
            "explaining why the pipeline failed. Currently missing."
        )
        assert "init" in data["error_message"].lower(), (
            f"The error message must explain the missing init stage. "
            f"Got: {data['error_message']}"
        )

    def test_missing_init_stage_row_fails_pipeline(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When advance_pipeline is called on a pipeline with
        current_stage = None and no 'init' stage row exists, the
        pipeline must be set to 'failed', logged at CRITICAL level,
        and _spawn_agent_container must NOT be called.

        From the production trace::

            ERROR  Pipeline stage row missing for expected stage 'init'
            INFO   Tearing down workspace

        The "Pipeline stage row missing" error must act as a terminal
        failure — the pipeline must not continue to spawn an agent
        container, copy sources, or perform any further work.

        Currently ``advance_pipeline`` logs this at ``"ERROR"`` level
        (line 294).  A missing stage row is a terminal, unrecoverable
        failure — it must be ``"CRITICAL"``.  This test proves the gap.
        """
        log_entries: list[tuple[str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: Any,
        ) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        pipeline = Pipeline.objects.create(
            invocation_name="missing-init-stage",
            description="Init stage row does not exist",
            status="running",
        )
        # Create stages for ALL entries EXCEPT "init".
        for name in orchestrator.STAGE_ORDER[1:]:
            PipelineStage.objects.create(
                pipeline=pipeline, name=name, status="pending",
            )
        pipeline.current_stage = None
        pipeline.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def track_spawn(
            _p: Pipeline, s: PipelineStage,
        ) -> tuple[int, bool]:
            spawn_calls.append(s.name)
            return (0, False)

        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container", track_spawn,
        )
        monkeypatch.setattr(
            orchestrator, "_create_workspace", lambda p: None,
        )

        orchestrator.advance_pipeline(pipeline)

        pipeline.refresh_from_db()
        assert pipeline.status == "failed", (
            "Pipeline must be 'failed' when init stage row is missing. "
            f"Got: {pipeline.status}"
        )

        assert spawn_calls == [], (
            "_spawn_agent_container must NOT be called when init stage "
            f"row is missing. Got: {spawn_calls}"
        )

        # ── Assert exactly ONE CRITICAL log entry.  A missing stage
        #    row is a terminal pipeline failure — ERROR is too weak.
        assert len(log_entries) >= 2, (
            "Expected at least two orchestrator log entries "
            f"(CRITICAL + Pipeline ended). Got: {len(log_entries)}"
        )
        level, msg = log_entries[-2]
        assert level == "CRITICAL", (
            f"Missing init stage row must log at CRITICAL level, "
            f"not {level}. Got: {level}"
        )
        assert "init" in msg, (
            f"Log message must mention the missing stage. Got: {msg}"
        )
        criticals = [lvl for lvl, _ in log_entries if lvl == "CRITICAL"]
        assert len(criticals) == 1, (
            f"Expected exactly 1 CRITICAL log entry for this pipeline "
            f"failure. Got {len(criticals)}: {criticals}"
        )
        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )


class TestInitStageRetryStuck:
    """When the init stage fails on the first attempt inside
    ``_execute_pipeline`` and then succeeds on a subsequent retry through
    ``advance_pipeline``, the pipeline must still advance to RED.

    ``_execute_pipeline`` (``orchestrator.py:314``) runs the init stage via
    ``advance_pipeline``, then uses an explicit bridge (line 361) to
    transition from init to RED.  If init fails on the first attempt,
    ``_handle_stage_failure`` rolls ``pipeline.current_stage`` back to
    ``None``.  The bridge check ``pipeline.current_stage == STAGE_ORDER[0]``
    fails (``None != "init"``), so the bridge is silently skipped.

    After the retry succeeds (through a subsequent ``advance_pipeline``
    call), ``_run_stage`` sets ``current_stage = "init"`` and init status
    to ``"pending"``.  The guard in ``advance_pipeline`` (line 579) then
    blocks any normal advancement from init because ``"pending"`` is not
    a terminal status.

    A second bridge inside ``advance_pipeline`` (line 650) catches the
    retry case: if ``stage.retry_count > 0`` and the pipeline is at init
    after ``_run_stage`` returns, it runs the next stage (RED) immediately.
    """

    def test_advances_past_init_after_retry(
        self,
        db: None,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """After init fails on the first attempt (inside
        _execute_pipeline) and succeeds on retry (via a subsequent
        advance_pipeline call), the pipeline MUST advance to RED.

        The retry bridge at orchestrator.py:650 fires when:
        1. stage is init (STAGE_ORDER[0])
        2. stage.retry_count > 0 (this is a retry)
        3. current_stage is still init after _run_stage returns

        Verifies the full flow: init fail → retry → bridge → RED start.
        """
        from apps.orchestrator.orchestrator import _execute_pipeline, advance_pipeline

        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="init-retry-stuck",
            description="Verify init retry does not get stuck",
            status="running",
            current_stage=None,
        )

        # Note: Do NOT create stage rows here.  _execute_pipeline calls
        # _create_stages internally (line 339), which creates one
        # PipelineStage per STAGE_ORDER entry.  Pre-creating stages would
        # cause a UniqueConstraint violation when _create_stages runs.

        # Create the state.json as _create_workspace → _init_state_file would
        state_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps({
            "pipeline_id": str(pipeline.id),
            "status": "running",
            "current_stage": None,
            "stages": {
                name: {"status": "pending", "output": None}
                for name in orchestrator.STAGE_ORDER
            },
            "updated_at": "t",
        }))

        # Track spawn calls — first fails, subsequent succeed
        spawn_call_count: int = 0

        def mock_spawn(p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            nonlocal spawn_call_count
            spawn_call_count += 1
            if spawn_call_count == 1:
                return (1, False)  # fail on first attempt
            return (0, False)      # succeed on retry

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_validate_stage_state", lambda p, s: (True, ""))
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # ── Act 1: Bootstrap via _execute_pipeline (production path) ─────
        _execute_pipeline(pipeline)
        pipeline.refresh_from_db()

        # ── Assert 1: init failed, pipeline still running, bridge skipped ─
        assert pipeline.status == "running", (
            "Pipeline should still be running after init failure + retry schedule"
        )
        init_stage = pipeline.stages.get(name="init")
        assert init_stage.status == "pending", (
            "Init stage should be pending (scheduled for retry)"
        )
        assert init_stage.retry_after is not None, (
            "Retry should be scheduled (retry_after set)"
        )
        assert pipeline.current_stage is None, (
            "current_stage rolled back to None after init failure — "
            "_execute_pipeline's first bridge check will fail (None != 'init')"
        )
        assert spawn_call_count == 1, (
            "Exactly one spawn attempt (the failing one) should have occurred"
        )

        # ── Act 2: Simulate retry_after expiry, then retry init ─────────
        # Clear the retry guard so advance_pipeline will attempt init again
        init_stage.retry_after = None
        init_stage.save(update_fields=["retry_after"])

        # Advance pipeline — the retry bridge should fire and advance to RED
        advance_pipeline(pipeline)
        pipeline.refresh_from_db()

        # The bridge fires: init retry succeeds (2nd spawn), then RED is
        # started (3rd spawn) in the same advance_pipeline call.
        assert spawn_call_count == 3, (
            f"Expected 3 spawns: init fail + init retry + RED via bridge. "
            f"Got {spawn_call_count}"
        )
        assert pipeline.current_stage in orchestrator.STAGE_ORDER[1:], (
            f"Pipeline should have advanced past init to {orchestrator.STAGE_ORDER[1:]}, "
            f"but got stuck at '{pipeline.current_stage}'"
        )
        init_stage.refresh_from_db()
        assert init_stage.status == "pending", (
            "Init stage should still be 'pending' after success — "
            "the init stage is the setup barrier and always stays 'pending'"
        )
        assert init_stage.retry_count > 0, (
            "Init stage should have retry_count > 0, confirming the bridge "
            "trigger condition (retry_count > 0) was met"
        )

        # ── Act 3: Normal advancement continues from RED onwards ─────────
        # After the bridge advanced to RED, a subsequent advance_pipeline
        # call should continue advancing normally (RED → GREEN → …).
        advance_pipeline(pipeline)
        pipeline.refresh_from_db()
        assert pipeline.current_stage != orchestrator.STAGE_ORDER[1], (
            f"Pipeline should have advanced past '{orchestrator.STAGE_ORDER[1]}' (RED) "
            f"on the next call, but got stuck at '{pipeline.current_stage}'"
        )

    def test_advances_past_init_retry_missing_red_fails_pipeline(
        self,
        db: None,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the init→RED retry bridge fires but the RED stage row
        is missing, the pipeline must be failed with a CRITICAL log.

        The bridge at orchestrator.py:650 tries to fetch STAGE_ORDER[1]
        after init retry succeeds.  If that stage row does not exist, the
        bridge must:
        - Log at CRITICAL level
        - Set pipeline to 'failed' with an explanatory error_message
        - Call _teardown_workspace
        - NOT call _spawn_agent_container for RED
        """
        from apps.orchestrator.orchestrator import advance_pipeline

        log_entries: list[tuple[str, str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: Any,
        ) -> None:
            log_entries.append((level, msg, pipeline.status))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="init-retry-missing-red",
            description="RED stage row missing after init retry succeeds",
            status="running",
            current_stage=None,
        )

        # Create ONLY the init stage — do NOT create RED (STAGE_ORDER[1])
        # so the bridge's DoesNotExist handler is exercised.
        PipelineStage.objects.create(
            pipeline=pipeline,
            name=orchestrator.STAGE_ORDER[0],
            status="pending",
            retry_count=1,  # bridge requires retry_count > 0
        )

        # Create state.json so _run_stage's init workspace guard is
        # not triggered (it would call _create_workspace etc.).
        state_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "state.json"
        state_file.write_text(json.dumps({
            "pipeline_id": str(pipeline.id),
            "status": "running",
            "current_stage": None,
            "stages": {
                name: {"status": "pending", "output": None}
                for name in orchestrator.STAGE_ORDER
            },
            "updated_at": "t",
        }))

        # Track spawn calls
        spawn_call_count: int = 0
        spawn_args: list[PipelineStage] = []

        def mock_spawn(p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            nonlocal spawn_call_count
            spawn_call_count += 1
            spawn_args.append(s)
            return (0, False)  # always succeed

        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container", mock_spawn,
        )
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state", lambda p, s: (True, ""),
        )
        monkeypatch.setattr(
            orchestrator, "_teardown_workspace", lambda p: None,
        )

        # ── Act: advance_pipeline — bridge fires, RED is missing ────────
        advance_pipeline(pipeline)
        pipeline.refresh_from_db()

        # ── Assert: pipeline failed, init spawned once, RED never ───────
        assert pipeline.status == "failed", (
            "Pipeline must be set to 'failed' when RED stage row "
            f"is missing after init retry. Got: {pipeline.status}"
        )
        # _transition_pipeline_state does NOT roll back current_stage;
        # it stays at whatever _run_stage set (in this case "init").
        # The pipeline status being "failed" is the authoritative signal.
        assert pipeline.current_stage == orchestrator.STAGE_ORDER[0], (
            "Pipeline current_stage reflects the last stage attempted "
            f"('{orchestrator.STAGE_ORDER[0]}') before the missing-RED "
            "error was detected"
        )
        assert spawn_call_count == 1, (
            f"_spawn_agent_container must be called exactly once (for init), "
            f"not for RED (which is missing). Got {spawn_call_count} calls"
        )
        assert spawn_args[0].name == orchestrator.STAGE_ORDER[0], (
            f"The only spawn should be for init, "
            f"got: {spawn_args[0].name}"
        )

        # ── Assert CRITICAL log about missing RED ───────────────────
        assert len(log_entries) >= 1, "Expected at least one log entry"
        # Find the CRITICAL entry about missing RED
        missing_entries = [
            (lvl, msg) for lvl, msg, _ in log_entries
            if lvl == "CRITICAL"
            and orchestrator.STAGE_ORDER[1] in msg
        ]
        assert len(missing_entries) == 1, (
            f"Expected exactly one CRITICAL log entry for missing "
            f"'{orchestrator.STAGE_ORDER[1]}' stage. "
            f"Got {len(missing_entries)}: {missing_entries}"
        )

        # ── Assert error_message is set on the pipeline ──────────────
        assert pipeline.error_message, (
            "Pipeline must have error_message set when failed by the "
            "bridge's DoesNotExist handler"
        )
        assert orchestrator.STAGE_ORDER[1] in pipeline.error_message, (
            f"error_message must mention '{orchestrator.STAGE_ORDER[1]}'. "
            f"Got: {pipeline.error_message}"
        )
