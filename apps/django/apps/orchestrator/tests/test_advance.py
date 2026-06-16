"""Tests for pipeline stage advancement and failure handling."""

from __future__ import annotations

import pytest
import json
import shutil
from pathlib import Path

from django.conf import settings
import django.utils.timezone as dj_timezone
from _pytest.monkeypatch import MonkeyPatch

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


class TestAdvanceOnFailure:
    def test_missing_next_stage_marks_pipeline_failed(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """advance_pipeline should not crash when the next stage row is
        missing from the DB; it should fail the pipeline gracefully.

        This covers the real-world scenario where orchestrator attempts
        PipelineStage.objects.get(name=next_stage_name) but the row does
        not exist.
        """
        current_stage = orchestrator.STAGE_ORDER[1]
        next_stage = orchestrator.STAGE_ORDER[2]

        pipeline_running.stages.filter(name=current_stage).update(
            status="completed"
        )
        pipeline_running.current_stage = current_stage
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        # Simulate a DB inconsistency: the next stage does not exist.
        pipeline_running.stages.filter(name=next_stage).delete()

        def _should_not_spawn(*_args, **_kwargs):
            pytest.fail("advance_pipeline must not spawn an agent")

        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container", _should_not_spawn
        )
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        orchestrator.advance_pipeline(pipeline_running)

        pipeline_running.refresh_from_db()
        assert pipeline_running.status == "failed"

    def test_advance_creates_missing_stages_when_pipeline_has_none(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When advance_pipeline encounters a pipeline with status='running'
        and NO PipelineStage rows at all, it should create the stages and
        proceed gracefully rather than immediately failing the pipeline.

        This covers the real-world scenario where the orchestrator crashes
        between setting a pipeline to 'running' and calling _create_stages.
        On restart, _reap_orphaned_pipelines sets the pipeline to 'failed',
        and if the user retries, advance_pipeline must recover gracefully.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="zero-stages-recovery",
            description="Pipeline with no stages — must be recovered",
            status="running",
        )
        # No stages created — pipeline.stages is empty

        # Mock all external side-effects that _run_stage would invoke
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container",
            lambda p, s: (0, False),
        )
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state",
            lambda p, s: (True, ""),
        )
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator.advance_pipeline(pipeline)

        # ── Assert ───────────────────────────────────────────────────────
        pipeline.refresh_from_db()

        # Stages must have been created (graceful recovery)
        stage_count = pipeline.stages.count()
        assert stage_count == len(orchestrator.STAGE_ORDER), (
            f"Expected {len(orchestrator.STAGE_ORDER)} stages to be created, "
            f"got {stage_count}"
        )

        # Pipeline must NOT be in 'failed' status — it should have completed
        # all stages gracefully (all mocks return success, so the recursive
        # chaining in _run_stage advances through all 6 stages).
        assert pipeline.status == "completed", (
            f"Pipeline should have recovered gracefully, got status="
            f"{pipeline.status!r}"
        )

        # Pipeline should have advanced through all stages to the final one
        assert pipeline.current_stage == orchestrator.STAGE_ORDER[-1], (
            f"Pipeline should have reached the final stage, got "
            f"current_stage={pipeline.current_stage!r}"
        )


class TestResilience:
    def test_survives_filesystem_deletion_during_advancement(
        self,
        pipeline_running: Pipeline,
        db: None,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """If the developer deletes the workspace directory while the
        orchestrator is advancing stages, the service should not crash.

        (This simulates `sudo rm -rf <WORKSPACE_ROOT>/<pipeline_id>/`.)
        """

        # Put the pipeline in a state where advance_pipeline will attempt
        # to run the *next* stage.
        for name in orchestrator.STAGE_ORDER[:2]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = orchestrator.STAGE_ORDER[1]
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        # Create a minimal state.json and then delete the workspace.
        state_dir = temp_workspace / str(pipeline_running.id) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "stages": {},
                    "current_stage": None,
                    "updated_at": "t",
                }
            )
        )
        shutil.rmtree(temp_workspace / str(pipeline_running.id), ignore_errors=True)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", lambda *_a, **_k: (1, False))
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda _p: None)

        # Should not raise; the stage failure path should handle missing
        # state.json (write_state_field becomes a no-op).
        orchestrator.advance_pipeline(pipeline_running)

        pipeline_running.refresh_from_db()
        assert pipeline_running.status == "running"

    def test_survives_abort_mid_stage_without_crashing_next_advancement(
        self,
        pipeline_running: Pipeline,
        db: None,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """If an abort occurs while a stage is executing and the stage
        completes, the orchestrator must not crash while attempting to
        advance further.

        Current bug: advance_pipeline recurses even after abort sets the
        pipeline status to 'cancelled'. If the next stage row is missing,
        it raises DoesNotExist.
        """

        # Ensure next stage is 'coder' and that it will complete.
        for name in orchestrator.STAGE_ORDER[:2]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = orchestrator.STAGE_ORDER[1]
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        # Remove the following stage row so recursion would normally crash.
        # current_stage=GREEN → next=REFRACTOR → following=compilance
        pipeline_running.stages.filter(name=orchestrator.STAGE_ORDER[3]).delete()

        # Provide state.json so validation for the completed stage passes.
        state_dir = temp_workspace / str(pipeline_running.id) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(
            json.dumps(
                {
                    "stages": {
                        orchestrator.STAGE_ORDER[2]: {"status": "completed", "output": None},
                    }
                }
            )
        )

        monkeypatch.setattr(orchestrator, "_stop_opencode_server", lambda _p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda _p: None)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda _p: None)

        def spawn_with_abort(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
            orchestrator.abort_pipeline(pipeline)
            return (0, False)  # stage completes

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", spawn_with_abort)

        orchestrator.advance_pipeline(pipeline_running)
        pipeline_running.refresh_from_db()

        assert pipeline_running.status == "cancelled"

    def test_guard_blocks_advancement_when_current_not_completed(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """The defensive guard in advance_pipeline refuses to advance when
        the current stage is not in a terminal state (completed/blocked).

        This protects against inconsistent DB state where a stage is
        marked pending/failed but pipeline.current_stage was not
        rolled back to the previous stage.
        """
        for name in orchestrator.STAGE_ORDER[:2]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = orchestrator.STAGE_ORDER[1]
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def mock_spawn(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(stage.name)
            return (1, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        # Use real _handle_stage_failure (with transaction.atomic) so
        # stage and pipeline state stay consistent.
        # Now monkeypatch _previous_stage_name to return the SAME stage
        # instead of the previous one — simulating the scenario where
        # pipeline.current_stage was not rolled back.
        def mock_prev(stage_name: str) -> str:
            return stage_name  # return self instead of prev

        monkeypatch.setattr(orchestrator, "_previous_stage_name", mock_prev)

        orchestrator.advance_pipeline(pipeline_running)
        assert spawn_calls[-1] == orchestrator.STAGE_ORDER[2]

        pipeline_running.refresh_from_db()

        # With _previous_stage_name returning "coder" instead of
        # "testing_align_red", pipeline.current_stage is now "coder"
        # (not completed).  The guard should block the second call.
        orchestrator.advance_pipeline(pipeline_running)

        assert spawn_calls[-1] == orchestrator.STAGE_ORDER[2], (
            f"Guard should have blocked advancement, but pipeline "
            f"advanced to {spawn_calls[-1]}"
        )

    def test_pipeline_retries_failed_stage(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """After a stage fails with the real _handle_stage_failure, the
        pipeline correctly retries the same stage on the next loop."""
        for name in orchestrator.STAGE_ORDER[:2]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = orchestrator.STAGE_ORDER[1]
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def mock_spawn(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(stage.name)
            return (1, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        # First call: runs "coder", which fails.  _handle_stage_failure
        # sets coder to "pending" and pipeline.current_stage to
        # "testing_align_red" (the previous stage).
        orchestrator.advance_pipeline(pipeline_running)
        assert spawn_calls[-1] == orchestrator.STAGE_ORDER[2]

        pipeline_running.refresh_from_db()

        # Verify the stage is pending with retry_after in the future.
        failing_stage = pipeline_running.stages.get(name=orchestrator.STAGE_ORDER[2])
        assert failing_stage.status == "pending"
        assert failing_stage.retry_after is not None
        assert failing_stage.retry_after > dj_timezone.now(), (
            "retry_after should be in the future"
        )

        # Second call: current_stage = "testing_align_red" (completed,
        # guard passes).  Next = "coder" which is pending with
        # retry_after in the future → blocked by retry guard.
        orchestrator.advance_pipeline(pipeline_running)
        assert len(spawn_calls) == 1, (
            f"Second advance should have been blocked by retry_after, "
            f"but coder was spawned again"
        )

    def test_pipeline_status_fails_on_max_retries(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        assert orchestrator.STAGE_ORDER == EXPECTED_STAGE_ORDER
        """When max retries are exceeded, pipeline status becomes 'failed'."""
        for name in orchestrator.STAGE_ORDER[:2]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = orchestrator.STAGE_ORDER[1]
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        failing_stage = pipeline_running.stages.get(name=orchestrator.STAGE_ORDER[2])
        failing_stage.retry_count = orchestrator.PIPELINE_MAX_RETRIES
        failing_stage.save(update_fields=["retry_count"])

        def mock_spawn(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
            return (1, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        orchestrator.advance_pipeline(pipeline_running)

        pipeline_running.refresh_from_db()
        failing_stage.refresh_from_db()

        assert failing_stage.status == "failed", (
            f"Stage should be 'failed' after max retries, got {failing_stage.status}"
        )
        assert pipeline_running.status == "failed", (
            f"Pipeline should be 'failed' after max retries, got {pipeline_running.status}"
        )
