"""Tests for pipeline stage advancement and failure handling."""

from __future__ import annotations

import django.utils.timezone as dj_timezone
from _pytest.monkeypatch import MonkeyPatch

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage


class TestAdvanceOnFailure:
    def test_guard_blocks_advancement_when_current_not_completed(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The defensive guard in advance_pipeline refuses to advance when
        the current stage is not in a terminal state (completed/blocked).

        This protects against inconsistent DB state where a stage is
        marked pending/failed but pipeline.current_stage was not
        rolled back to the previous stage.
        """
        for name in ["planner", "plan_reviewer", "test_builder", "testing_align_red"]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = "testing_align_red"
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
        assert spawn_calls[-1] == "coder"

        pipeline_running.refresh_from_db()

        # With _previous_stage_name returning "coder" instead of
        # "testing_align_red", pipeline.current_stage is now "coder"
        # (not completed).  The guard should block the second call.
        orchestrator.advance_pipeline(pipeline_running)

        assert spawn_calls[-1] == "coder", (
            f"Guard should have blocked advancement, but pipeline "
            f"advanced to {spawn_calls[-1]}"
        )

    def test_pipeline_retries_failed_stage(
        self,
        pipeline_running: Pipeline,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """After a stage fails with the real _handle_stage_failure, the
        pipeline correctly retries the same stage on the next loop."""
        for name in ["planner", "plan_reviewer", "test_builder", "testing_align_red"]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = "testing_align_red"
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
        assert spawn_calls[-1] == "coder"

        pipeline_running.refresh_from_db()

        # Verify the stage is pending with retry_after in the future.
        coder_stage = pipeline_running.stages.get(name="coder")
        assert coder_stage.status == "pending"
        assert coder_stage.retry_after is not None
        assert coder_stage.retry_after > dj_timezone.now(), (
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
        """When max retries are exceeded, pipeline status becomes 'failed'."""
        for name in ["planner", "plan_reviewer", "test_builder", "testing_align_red"]:
            pipeline_running.stages.filter(name=name).update(status="completed")
        pipeline_running.current_stage = "testing_align_red"
        pipeline_running.save(update_fields=["current_stage", "updated_at"])

        coder_stage = pipeline_running.stages.get(name="coder")
        coder_stage.retry_count = 3  # already at max (PIPELINE_MAX_RETRIES=3)
        coder_stage.save(update_fields=["retry_count"])

        def mock_spawn(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
            return (1, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_create_workspace", lambda p: None)

        orchestrator.advance_pipeline(pipeline_running)

        pipeline_running.refresh_from_db()
        coder_stage.refresh_from_db()

        assert coder_stage.status == "failed", (
            f"Stage should be 'failed' after max retries, got {coder_stage.status}"
        )
        assert pipeline_running.status == "failed", (
            f"Pipeline should be 'failed' after max retries, got {pipeline_running.status}"
        )
