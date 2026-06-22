"""Session-based stage executor for the opencode server pipeline.

Replaces the old ``_run_stage`` polling-loop approach with an HTTP-driven
flow that creates a session, sends a stage prompt via the opencode server
API, and inspects the ``MessageResponse`` parts to determine completion,
blocked, or failure outcomes.

DB persistence is the caller's responsibility — this module updates
in-memory attributes only.  See the async orchestrator (Cycle 6) for
``sync_to_async`` wrappers around ``pipeline.save()`` and
``stage.save()``.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from django.utils import timezone as dj_timezone

from apps.orchestrator.agent_client import (
    AgentClient,
    AgentClientError,
    is_blocked,
    has_error,
)
from apps.orchestrator.models import Pipeline, PipelineStage

logger = logging.getLogger(__name__)


class StageResult(enum.Enum):
    """Outcome of a stage execution."""

    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


def build_stage_prompt(
    pipeline: Pipeline,
    stage: PipelineStage,
) -> list[dict[str, Any]]:
    """Construct the message parts to send for *stage*.

    The prompt includes the stage name, pipeline context, and guidance for
    how the agent should report its progress.
    """
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"You are executing stage: {stage.name}.\n"
                f"Pipeline: {pipeline.invocation_name} "
                f"(status: {pipeline.status}).\n"
                f"Write your results to the state file when done. "
                f"Report your progress and any decisions made."
            ),
        },
    ]
    return parts


async def execute_stage(
    pipeline: Pipeline,
    stage: PipelineStage,
    client: AgentClient,
) -> StageResult:
    """Execute a single pipeline stage via the opencode HTTP server.

    Updates *stage* and *pipeline* in-memory attributes (``status``,
    ``session_id``, ``started_at``, ``finished_at``,
    ``user_input_pending``).  The caller is responsible for persisting
    these changes to the database.

    Parameters
    ----------
    pipeline:
        The pipeline being executed.
    stage:
        The stage within *pipeline* to execute.
    client:
        An ``AgentClient`` connected to the pipeline's opencode server.

    Returns
    -------
    StageResult
        ``COMPLETED``, ``BLOCKED``, or ``FAILED``.
    """
    now = dj_timezone.now()

    # ── Mark stage as running ──────────────────────────────────────────
    stage.status = "running"
    stage.started_at = now
    stage.retry_after = None

    # ── Create session ─────────────────────────────────────────────────
    try:
        session_id = await client.create_session(title=stage.name)
    except AgentClientError as exc:
        logger.error("Failed to create session for stage %s: %s", stage.name, exc)
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Save session ID (in-memory; caller persists) ───────────────────
    stage.session_id = session_id

    # ── Build and send prompt ──────────────────────────────────────────
    prompt_parts = build_stage_prompt(pipeline, stage)

    try:
        response = await client.send_message(
            session_id,
            parts=prompt_parts,
        )
    except AgentClientError as exc:
        logger.error(
            "Failed to send message for stage %s (session %s): %s",
            stage.name, session_id, exc,
        )
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Inspect response for blocked / error markers ───────────────────
    if is_blocked(response):
        stage.status = "blocked"
        stage.finished_at = dj_timezone.now()
        pipeline.user_input_pending = True
        return StageResult.BLOCKED

    if has_error(response):
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Default: completed ─────────────────────────────────────────────
    stage.status = "completed"
    stage.finished_at = dj_timezone.now()
    stage.retry_after = None

    return StageResult.COMPLETED
