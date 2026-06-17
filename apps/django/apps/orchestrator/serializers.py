"""DRF-style serializers for Pipeline and PipelineStage.

Converts Django model instances to JSON-serializable dicts.
Used by API views to produce consistent JSON responses.
"""

from apps.orchestrator.models import Pipeline, PipelineStage


def pipeline_to_dict(pipeline: Pipeline) -> dict:
    return {
        "id": str(pipeline.id),
        "invocation_name": pipeline.invocation_name,
        "status": pipeline.status,
        "current_stage": pipeline.current_stage,
        "error_message": pipeline.error_message,
        "iteration_count": pipeline.iteration_count,
        "user_input_pending": pipeline.user_input_pending,
        "user_input_request": pipeline.user_input_request,
        "pr_url": pipeline.pr_url or "",
        "description": pipeline.description,
        "created_at": pipeline.created_at.isoformat(),
        "updated_at": pipeline.updated_at.isoformat(),
    }


def stage_to_dict(stage: PipelineStage) -> dict:
    return {
        "id": stage.id,
        "name": stage.name,
        "status": stage.status,
        "output": stage.output,
        "retry_count": stage.retry_count,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "finished_at": stage.finished_at.isoformat() if stage.finished_at else None,
    }
