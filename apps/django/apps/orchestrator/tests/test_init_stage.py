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
        assert len(log_entries) >= 1, (
            "Expected at least one orchestrator log entry"
        )
        level, msg = log_entries[-1]
        assert level == "CRITICAL", (
            f"Terminal init-stage failures must log at CRITICAL level "
            f"so operators can distinguish them from non-terminal "
            f"errors. Got: {level}"
        )
        assert "Non-OSError" in msg, (
            f"Log message must describe the failure. Got: {msg}"
        )
        # Ensure no double-failure: the last entry IS the failure; if
        # there was a prior CRITICAL from the same pipeline incident
        # something went wrong.
        criticals = [lvl for lvl, _ in log_entries if lvl == "CRITICAL"]
        assert len(criticals) == 1, (
            f"Expected exactly 1 CRITICAL log entry for this pipeline "
            f"failure. Got {len(criticals)}: {criticals}"
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
        assert len(log_entries) >= 1, (
            "Expected at least one orchestrator log entry"
        )
        level, msg = log_entries[-1]
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
