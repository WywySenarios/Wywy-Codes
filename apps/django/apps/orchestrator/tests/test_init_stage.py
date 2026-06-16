"""Tests for the pipeline initialization stage.

The init stage is the first stage in every pipeline. It handles workspace
creation, source tree copying, and opencode server startup.
"""

from __future__ import annotations

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline


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
