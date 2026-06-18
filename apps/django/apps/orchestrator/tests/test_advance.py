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
            raise Exception("Container killed by abort")  # simulate abort killing the container

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


class TestIterativeAdvancement:
    """advance_pipeline must advance exactly one stage per call (iterative).

    The orchestrator loop calls advance_pipeline every tick; it must not
    chain recursively through all remaining stages.
    """

    def test_advances_one_stage_per_call(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """After a stage completes, advance_pipeline must stop and return
        rather than recursively chaining to the next stage."""
        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="iterative-test",
            description="Verify one stage per call",
            status="running",
        )
        for name in orchestrator.STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
        # Mark first stage completed so advance_pipeline proceeds to stage 1
        pipeline.stages.filter(name=orchestrator.STAGE_ORDER[0]).update(status="completed")
        pipeline.current_stage = orchestrator.STAGE_ORDER[0]
        pipeline.save(update_fields=["current_stage", "updated_at"])

        # Track how many times _spawn_agent_container is called
        spawn_calls: list[str] = []

        def track_spawn(_p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(s.name)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", track_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
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
        # Iterative: exactly one stage must be spawned
        assert len(spawn_calls) == 1, (
            f"Expected 1 spawn (iterative), got {len(spawn_calls)}: "
            f"{', '.join(spawn_calls)}"
        )
        assert spawn_calls[0] == orchestrator.STAGE_ORDER[1], (
            f"Expected stage {orchestrator.STAGE_ORDER[1]} to be spawned, "
            f"got {spawn_calls[0]}"
        )

        pipeline.refresh_from_db()
        assert pipeline.status == "running", (
            f"Pipeline should still be running after one advance, "
            f"got {pipeline.status}"
        )

        # Verify later stages are untouched
        for name in orchestrator.STAGE_ORDER[2:]:
            st = pipeline.stages.get(name=name)
            assert st.status == "pending", (
                f"Stage '{name}' should still be pending after one "
                f"advance call, got {st.status}"
            )

    def test_n_calls_advance_n_stages_to_completion(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Calling advance_pipeline repeatedly advances one stage per call
        until the pipeline reaches 'completed'."""
        # ── Arrange ──────────────────────────────────────────────────────
        pipeline = Pipeline.objects.create(
            invocation_name="n-calls-test",
            description="Verify N calls = N stages",
            status="running",
        )
        for name in orchestrator.STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
        # Mark first stage completed so advance proceeds to stage 1
        pipeline.stages.filter(name=orchestrator.STAGE_ORDER[0]).update(status="completed")
        pipeline.current_stage = orchestrator.STAGE_ORDER[0]
        pipeline.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def track_spawn(_p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(s.name)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", track_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state",
            lambda p, s: (True, ""),
        )
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        remaining = len(orchestrator.STAGE_ORDER) - 1  # first is already done

        # ── Act: call advance_pipeline for each remaining stage + one
        # more to trigger _complete_pipeline (the "no next stage" check
        # only fires on the call *after* the last stage completes).
        # total calls = remaining + 1
        for i in range(remaining + 1):
            orchestrator.advance_pipeline(pipeline)
            pipeline.refresh_from_db()

            if i < remaining:
                # This call should have spawned one stage
                assert len(spawn_calls) == i + 1, (
                    f"After call {i+1}, expected {i+1} spawns, "
                    f"got {len(spawn_calls)}"
                )
                expected_stage = orchestrator.STAGE_ORDER[i + 1]
                assert spawn_calls[i] == expected_stage, (
                    f"Call {i+1} should have spawned {expected_stage}, "
                    f"got {spawn_calls[i]}"
                )
                # Pipeline should still be running (last stage not consumed yet)
                assert pipeline.status == "running", (
                    f"After call {i+1}, pipeline should still be "
                    f"running, got {pipeline.status}"
                )

        # ── Assert: all stages completed, pipeline done ──────────────────
        pipeline.refresh_from_db()
        assert pipeline.status == "completed", (
            f"Pipeline should be completed after {remaining + 1} calls, "
            f"got {pipeline.status}"
        )
        assert len(spawn_calls) == remaining, (
            f"Expected {remaining} total spawns (last call does not spawn), "
            f"got {len(spawn_calls)}"
        )
        for name in orchestrator.STAGE_ORDER:
            st = pipeline.stages.get(name=name)
            assert st.status == "completed", (
                f"Stage '{name}' should be completed, got {st.status}"
            )

    def test_advance_on_completed_pipeline_is_noop(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_completed: Pipeline,
    ) -> None:
        """Calling advance_pipeline on an already-completed pipeline
        must not change any state or spawn any agents."""
        spawn_calls: list[str] = []

        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container",
            lambda _p, _s: (_ for _ in ()).throw(
                AssertionError("Must not spawn on completed pipeline")
            ),
        )
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        status_before = pipeline_completed.status

        orchestrator.advance_pipeline(pipeline_completed)

        pipeline_completed.refresh_from_db()
        assert pipeline_completed.status == status_before, (
            f"Status should remain '{status_before}', "
            f"got {pipeline_completed.status}"
        )

    def test_advance_on_failed_pipeline_is_noop(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_failed: Pipeline,
    ) -> None:
        """Calling advance_pipeline on a failed pipeline must not
        change any state or spawn any agents."""
        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container",
            lambda _p, _s: (_ for _ in ()).throw(
                AssertionError("Must not spawn on failed pipeline")
            ),
        )
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        status_before = pipeline_failed.status

        orchestrator.advance_pipeline(pipeline_failed)

        pipeline_failed.refresh_from_db()
        assert pipeline_failed.status == status_before, (
            f"Status should remain '{status_before}', "
            f"got {pipeline_failed.status}"
        )

    def test_last_stage_triggers_completion(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When advance_pipeline runs the last stage and finds no next
        stage, it must call _complete_pipeline and mark the pipeline
        as completed."""
        # ── Arrange: all stages up to penultimate completed, final pending ──
        pipeline = Pipeline.objects.create(
            invocation_name="last-stage-test",
            description="Verify last stage triggers completion",
            status="running",
        )
        for name in orchestrator.STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline, name=name, status="completed")
        penultimate = orchestrator.STAGE_ORDER[-2]
        final_stage = orchestrator.STAGE_ORDER[-1]
        # Reset final stage to pending so it will be executed
        pipeline.stages.filter(name=final_stage).update(status="pending")
        pipeline.current_stage = penultimate
        pipeline.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def track_spawn(_p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(s.name)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", track_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state",
            lambda p, s: (True, ""),
        )
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # ── Act 1: advance to final stage ────────────────────────────────
        # advance_pipeline runs the last stage (GREEN in conftest fixture,
        # but here it's the last stage in STAGE_ORDER)
        orchestrator.advance_pipeline(pipeline)
        pipeline.refresh_from_db()

        # The last stage should have been spawned and completed
        assert len(spawn_calls) == 1, (
            f"Expected 1 spawn for the final stage, got {len(spawn_calls)}"
        )
        assert spawn_calls[0] == final_stage, (
            f"Expected final stage '{final_stage}' to be spawned, "
            f"got {spawn_calls[0]}"
        )
        last = pipeline.stages.get(name=final_stage)
        assert last.status == "completed", (
            f"Final stage should be completed, got {last.status}"
        )
        # Pipeline still running — _complete_pipeline hasn't been called yet
        assert pipeline.status == "running", (
            f"Pipeline should still be running after final stage runs, "
            f"got {pipeline.status}"
        )
        # current_stage should now be the final stage
        assert pipeline.current_stage == final_stage, (
            f"current_stage should be '{final_stage}', "
            f"got {pipeline.current_stage}"
        )

        # ── Act 2: complete the pipeline ─────────────────────────────────
        orchestrator.advance_pipeline(pipeline)
        pipeline.refresh_from_db()

        # No new spawns — the final stage is already completed,
        # and there's no next stage, so _complete_pipeline fires
        assert len(spawn_calls) == 1, (
            f"No new spawns expected during completion, "
            f"got {len(spawn_calls)}"
        )
        assert pipeline.status == "completed", (
            f"Pipeline should be completed after final stage, "
            f"got {pipeline.status}"
        )

    def test_no_current_stage_starts_at_init(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When current_stage is None (fresh pipeline), advance_pipeline
        must start with the first stage in STAGE_ORDER (init)."""
        pipeline = Pipeline.objects.create(
            invocation_name="no-current-stage",
            description="Start from init when current_stage is None",
            status="running",
        )
        for name in orchestrator.STAGE_ORDER:
            PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
        # Explicitly set current_stage to None
        pipeline.current_stage = None
        pipeline.save(update_fields=["current_stage", "updated_at"])

        spawn_calls: list[str] = []

        def track_spawn(_p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            spawn_calls.append(s.name)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", track_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(
            orchestrator, "_validate_stage_state",
            lambda p, s: (True, ""),
        )
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        orchestrator.advance_pipeline(pipeline)

        assert len(spawn_calls) == 1, (
            f"Expected 1 spawn for init stage, got {len(spawn_calls)}"
        )
        assert spawn_calls[0] == orchestrator.STAGE_ORDER[0], (
            f"Expected first stage '{orchestrator.STAGE_ORDER[0]}' to be "
            f"spawned, got {spawn_calls[0]}"
        )

        pipeline.refresh_from_db()
        assert pipeline.status == "running", (
            f"Pipeline should still be running after init, "
            f"got {pipeline.status}"
        )
        init_stage = pipeline.stages.get(name=orchestrator.STAGE_ORDER[0])
        assert init_stage.status == "completed", (
            f"Init stage should be completed, got {init_stage.status}"
        )
        assert pipeline.current_stage == orchestrator.STAGE_ORDER[0], (
            f"current_stage should be '{orchestrator.STAGE_ORDER[0]}', "
            f"got {pipeline.current_stage}"
        )


class TestNoRetryOnDeadPipeline:
    """_handle_stage_failure must NOT schedule a retry when the pipeline
    is already in a terminal state (failed/cancelled).

    A race exists: the abort API view runs on a different thread and can
    set ``pipeline.status = "cancelled"`` while the orchestrator loop is
    blocked inside ``_run_stage`` waiting for the agent.  When the HTTP
    call eventually fails, ``_handle_stage_failure`` is called with a
    *stale* in-memory pipeline object whose status is still ``"running"``.
    Therefore the guard must **refresh the pipeline from the database**
    before deciding whether to schedule a retry.
    """

    def test_no_retry_when_pipeline_already_failed(
        self,
        pipeline_failed: Pipeline,
        db: None,
    ) -> None:
        """When a pipeline already has a 'failed' status, _handle_stage_failure
        must not schedule a retry — it must mark the stage as failed and return
        without rolling back current_stage."""
        stage = PipelineStage.objects.create(
            pipeline=pipeline_failed,
            name="GREEN",
            status="running",
            retry_count=0,
        )

        orchestrator._handle_stage_failure(pipeline_failed, stage)

        stage.refresh_from_db()
        pipeline_failed.refresh_from_db()

        # Stage must NOT be pending (which signals a retry)
        assert stage.status == "failed", (
            f"Stage should be 'failed', got '{stage.status}' — "
            f"retry was scheduled despite pipeline being dead"
        )
        # No retry_after should be set
        assert stage.retry_after is None, (
            f"retry_after must be None, got {stage.retry_after}"
        )
        # current_stage must NOT be rolled back
        assert pipeline_failed.current_stage == "GREEN", (
            f"current_stage should still be 'GREEN', "
            f"got '{pipeline_failed.current_stage}'"
        )

    def test_no_retry_when_pipeline_already_cancelled(
        self,
        pipeline_cancelled: Pipeline,
        db: None,
    ) -> None:
        """Same invariant for a 'cancelled' pipeline."""
        stage = PipelineStage.objects.create(
            pipeline=pipeline_cancelled,
            name="init",
            status="running",
            retry_count=0,
        )

        orchestrator._handle_stage_failure(pipeline_cancelled, stage)

        stage.refresh_from_db()
        pipeline_cancelled.refresh_from_db()

        assert stage.status == "failed", (
            f"Stage should be 'failed', got '{stage.status}' — "
            f"retry was scheduled despite pipeline being cancelled"
        )
        assert stage.retry_after is None, (
            f"retry_after must be None, got {stage.retry_after}"
        )
        # current_stage should remain unchanged (init → None)
        assert pipeline_cancelled.current_stage is None, (
            f"current_stage should be None, "
            f"got '{pipeline_cancelled.current_stage}'"
        )
