"""Session-based stage executor for the opencode server pipeline.

Creates a session, sends a stage prompt via the opencode server API,
retrieves the full response parts, and inspects them for completion,
blocked, or failure outcomes.

DB persistence is the caller's responsibility — this module updates
in-memory attributes only.  The orchestrator synchronises these to
the database with ``sync_to_async`` wrappers.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from django.conf import settings
from django.utils import timezone as dj_timezone

from opencode_ai import APIStatusError, AsyncOpencode
from opencode_ai.types import AssistantMessage, Part, ToolPart

from apps.orchestrator.models import Pipeline, PipelineStage

logger = logging.getLogger(__name__)


# ── Part inspection helpers ──────────────────────────────────────────


def is_blocked(parts: list[Part]) -> bool:
    """Return ``True`` when the response indicates the stage is blocked.

    Returns ``True`` when any part is a ``ToolPart`` with
    ``state.status == "pending"``.
    """
    return any(
        isinstance(p, ToolPart)
        and getattr(p.state, "status", None) == "pending"
        for p in parts
    )


def has_error(message: AssistantMessage) -> bool:
    """Return ``True`` when the ``AssistantMessage`` has a non-``None`` error."""
    return message.error is not None


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


def _resolve_provider(model_id: str) -> str:
    """Resolve a provider ID from a model identifier.

    Returns the part before the first ``/`` (e.g. ``"deepseek/deepseek-chat"``
    → ``"deepseek"``).
    """
    return model_id.split("/")[0]


async def execute_stage(
    pipeline: Pipeline,
    stage: PipelineStage,
    client: AsyncOpencode,
) -> StageResult:
    """Execute a single pipeline stage via the opencode HTTP server.

    Two-step flow:
    1. Create a session (``client.session.create()``).
    2. Send the prompt (``client.session.chat()``).
    3. Inspect ``session.chat`` response for errors.
    4. Retrieve full parts via ``client.session.messages()``.
    5. Inspect parts for blocked / completion markers.

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
        An ``AsyncOpencode`` connected to the pipeline's opencode server.

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
        session = await client.session.create()
    except APIStatusError as exc:
        logger.error(
            "Failed to create session for stage %s: %s", stage.name, exc
        )
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Save session ID (in-memory; caller persists) ───────────────────
    stage.session_id = session.id

    # ── Build and send prompt ──────────────────────────────────────────
    prompt_parts = build_stage_prompt(pipeline, stage)

    model_id = settings.STAGE_MODEL_MAP.get(stage.name, {}).get(
        "model", settings.OPENCODE_DEFAULT_MODEL
    )
    provider_id = _resolve_provider(model_id)

    try:
        response = await client.session.chat(
            session.id,
            model_id=model_id,
            provider_id=provider_id,
            parts=prompt_parts,
        )
    except APIStatusError as exc:
        logger.error(
            "Failed to send message for stage %s (session %s): %s",
            stage.name, session.id, exc,
        )
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Check for error on the chat response ───────────────────────────
    if response.error:
        stage.status = "failed"
        stage.finished_at = dj_timezone.now()
        return StageResult.FAILED

    # ── Retrieve parts via messages() (two-step SDK flow) ──────────────
    items = await client.session.messages(session.id)
    latest_parts = items[-1].parts if items else []

    # ── Inspect parts for blocked marker ───────────────────────────────
    if is_blocked(latest_parts):
        stage.status = "blocked"
        stage.finished_at = dj_timezone.now()
        pipeline.user_input_pending = True
        return StageResult.BLOCKED

    # ── Default: completed ─────────────────────────────────────────────
    stage.status = "completed"
    stage.finished_at = dj_timezone.now()
    stage.retry_after = None

    return StageResult.COMPLETED
