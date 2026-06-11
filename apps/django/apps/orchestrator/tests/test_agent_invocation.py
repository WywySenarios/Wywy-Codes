"""Parameterized test: verify agent invocation at every pipeline stage.

One basic pattern driven by a constant table — for each of the 9 stages,
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
    ("planner",           "",                 []),
    ("plan_reviewer",     "planner",          ["planner"]),
    ("test_builder",      "plan_reviewer",    ["planner", "plan_reviewer"]),
    ("testing_align_red", "test_builder",     ["planner", "plan_reviewer", "test_builder"]),
    ("coder",             "testing_align_red", ["planner", "plan_reviewer", "test_builder", "testing_align_red"]),
    ("code_reviewer",     "coder",            ["planner", "plan_reviewer", "test_builder", "testing_align_red",
                                                "coder"]),
    ("testing_green",     "code_reviewer",    ["planner", "plan_reviewer", "test_builder", "testing_align_red",
                                                "coder", "code_reviewer"]),
    ("pr_writer",         "testing_green",    ["planner", "plan_reviewer", "test_builder", "testing_align_red",
                                                "coder", "code_reviewer", "testing_green"]),
    ("pr_reviewer",       "pr_writer",        ["planner", "plan_reviewer", "test_builder", "testing_align_red",
                                                "coder", "code_reviewer", "testing_green", "pr_writer"]),
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

        # 7. Run — single advance_pipeline chains through all remaining stages
        orchestrator.advance_pipeline(pipeline)

        # 8. Verify target stage was spawned first
        assert len(spawn_calls) > 0, f"No agent spawned for stage {stage_name}"
        spawned_pipeline, spawned_stage = spawn_calls[0]
        assert spawned_stage.name == stage_name, (
            f"Expected first spawn to be {stage_name}, got {spawned_stage.name}"
        )

        # 9. Verify call context
        assert spawned_pipeline.id == pipeline.id
        assert spawned_pipeline.invocation_name == f"agent-test-{stage_name}"
        assert spawned_stage.name == stage_name

        # 10. Verify pipeline completed
        pipeline.refresh_from_db()
        assert pipeline.status == "completed", (
            f"Expected completed, got {pipeline.status}"
        )
        for name in orchestrator.STAGE_ORDER:
            st = pipeline.stages.get(name=name)
            assert st.status == "completed", (
                f"Stage {name} should be completed, got {st.status}"
            )

        # Verify spawn count covers all remaining stages (target through pr_reviewer)
        remaining_idx = orchestrator.STAGE_ORDER.index(stage_name)
        remaining_count = len(orchestrator.STAGE_ORDER) - remaining_idx
        assert len(spawn_calls) == remaining_count, (
            f"Expected {remaining_count} spawns for stages {stage_name}..pr_reviewer, "
            f"got {len(spawn_calls)}: {[s.name for _, s in spawn_calls]}"
        )
        for i, (_, s) in enumerate(spawn_calls):
            expected_name = orchestrator.STAGE_ORDER[remaining_idx + i]
            assert s.name == expected_name, (
                f"Spawn call {i}: expected {expected_name}, got {s.name}"
            )
