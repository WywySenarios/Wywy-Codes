"""Async orchestrator loop for pipeline lifecycle management.

Cycle 6 of the opencode-server pipeline: replaces the daemon thread with
an asyncio-based orchestrator process that manages the full pipeline
lifecycle — picking queued pipelines, starting containers, executing
stages via the ``execute_stage`` coroutine, and cleaning up.

CONVENTION-EXCEPTION: ``DJANGO_ALLOW_ASYNC_UNSAFE`` is set at module level
because these coroutines call the Django ORM synchronously from an async
context (the standard Django escape hatch for intentional async-unsafe
usage — ``SynchronousOnlyOperation`` errors are blocked by this variable).
"""

from __future__ import annotations

import asyncio
import logging
import os

# ── Allow sync ORM calls from async context ─────────────────────────────
# Without this, Django raises SynchronousOnlyOperation when the ORM is
# called from an async context.  This is the documented escape hatch.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.conf import settings

from django.db.utils import OperationalError

from apps.orchestrator.container_manager import ContainerManager
from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.orchestrator import (
    _create_pr,
    _teardown_workspace,
)
from apps.orchestrator.stage_executor import execute_stage

logger = logging.getLogger(__name__)

# ── Wake signal ──────────────────────────────────────────────────────────
# Set this ``asyncio.Event`` to trigger an immediate queue check in the
# ``main()`` loop.
wake_event: asyncio.Event = asyncio.Event()


# ── Public coroutines ────────────────────────────────────────────────────


async def main() -> None:
    """Main async orchestrator loop.

    Reaps orphaned pipelines on startup, then loops:
    * Picks all ``"queued"`` pipelines and spawns ``run_pipeline`` tasks
      (concurrency limited by ``MAX_ACTIVE_PIPELINES`` semaphore).
    * Waits for ``wake_event`` or a 5-second timeout before re-checking.
    """
    logger.info("Async orchestrator loop started")

    await reap_orphaned_pipelines()

    semaphore = asyncio.Semaphore(settings.MAX_ACTIVE_PIPELINES)

    while True:
        queued = list(
            Pipeline.objects.filter(status="queued").order_by("created_at")
        )
        for pipeline in queued:
            asyncio.create_task(run_pipeline(pipeline, semaphore))

        # Wait for wake signal or timeout
        try:
            await asyncio.wait_for(wake_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        else:
            wake_event.clear()


async def run_pipeline(
    pipeline: Pipeline,
    semaphore: asyncio.Semaphore | None = None,
) -> None:
    """Run a pipeline through all its stages.

    Acquires *semaphore* (if given) before starting, then:
    1. Transitions ``"queued"`` → ``"running"``.
    2. Starts the opencode server container via ``ContainerManager``.
    3. Waits for the container to become healthy.
    4. Iterates over all ``PipelineStage`` records, calling
       ``execute_stage`` for each.
    5. Tears down the workspace, attempts PR creation, and stops the
       container.
    """
    async def _run() -> None:
        # ── Transition to running ───────────────────────────────────────
        _maybe_set_running(pipeline)

        # ── Start container ─────────────────────────────────────────────
        cm = ContainerManager()
        container_id = cm.start_container(pipeline)
        client = await cm.wait_healthy(container_id)

        # ── Execute stages ──────────────────────────────────────────────
        stages = list(pipeline.stages.all())
        for stage in stages:
            await execute_stage(pipeline, stage, client)

        # ── Cleanup ─────────────────────────────────────────────────────
        _teardown_workspace(pipeline)
        _create_pr(pipeline)
        cm.stop_container(pipeline)

    if semaphore:
        async with semaphore:
            await _run()
    else:
        await _run()


def _maybe_set_running(pipeline: Pipeline) -> None:
    """Transition pipeline to running if it was queued.

    This is a SYNC helper so it can be called from the event loop thread
    without thread-pool DB conflicts.  The save is best-effort: if the
    database is locked the transition is skipped silently — the caller
    holds the semaphore so no other task races.
    """
    if pipeline.status == "queued":
        pipeline.status = "running"
        try:
            pipeline.save(update_fields=["status", "updated_at"])
        except OperationalError:
            logger.warning(
                "Could not persist queued→running transition for pipeline %s "
                "(DB locked)",
                pipeline.id,
            )


async def handle_stage_failure(
    pipeline: Pipeline,
    stage: PipelineStage,
) -> None:
    """Handle a failed stage with retry logic.

    Updates **in-memory** attributes on *stage* and *pipeline*.  The
    caller is responsible for persistence (see Cycle 5 design notes).
    DB writes in this function are best-effort and failures are logged
    but suppressed — the in-memory changes are always applied so the
    caller can persist them later.
    """
    stage.retry_count += 1

    if stage.retry_count > settings.PIPELINE_MAX_RETRIES:
        stage.status = "failed"
        pipeline.status = "failed"
        _maybe_save(stage, ["status", "retry_count"])
        _maybe_save(pipeline, ["status", "updated_at"])
    else:
        _maybe_save(stage, ["retry_count"])


def _maybe_save(instance: Pipeline | PipelineStage, fields: list[str]) -> None:
    """Best-effort save: persist *fields* or log the failure."""
    try:
        instance.save(update_fields=fields)
    except OperationalError:
        logger.warning(
            "Could not persist %s.%s (DB locked)",
            type(instance).__name__,
            fields,
        )


async def reap_orphaned_pipelines() -> None:
    """Transition any ``"running"`` pipelines to ``"failed"`` on startup.

    When the orchestrator restarts, any pipeline that was left in
    ``"running"`` state must be considered orphaned and reset so it
    does not block new pipeline starts.
    """
    for pipeline in Pipeline.objects.filter(status="running"):
        pipeline.status = "failed"
        _maybe_save(pipeline, ["status", "updated_at"])
