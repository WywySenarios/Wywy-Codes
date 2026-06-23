"""Tests for the async orchestrator loop.

Cycle 6 of the opencode-server pipeline: replace the daemon thread with
an asyncio-based orchestrator process that manages the full pipeline
lifecycle — picking queued pipelines, starting containers, executing
stages, and cleaning up.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from opencode_ai import AsyncOpencode
from apps.orchestrator.models import Pipeline, PipelineStage


# ═══════════════════════════════════════════════════════════════════════
# RED: module must exist
# ═══════════════════════════════════════════════════════════════════════


def test_orchestrator_async_module_exists() -> None:
    """The ``orchestrator_async`` module must exist."""
    import apps.orchestrator.orchestrator_async  # noqa: F401


def test_main_function_exists() -> None:
    """``orchestrator_async`` must expose a ``main`` coroutine."""
    from apps.orchestrator.orchestrator_async import main  # noqa: F401


def test_run_pipeline_function_exists() -> None:
    """``orchestrator_async`` must expose a ``run_pipeline`` coroutine."""
    from apps.orchestrator.orchestrator_async import run_pipeline  # noqa: F401


def test_handle_stage_failure_function_exists() -> None:
    """``orchestrator_async`` must expose a ``handle_stage_failure`` coroutine."""
    from apps.orchestrator.orchestrator_async import (
        handle_stage_failure,  # noqa: F401
    )


# ═══════════════════════════════════════════════════════════════════════
# RED: orphan reaping
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_reap_orphaned_mark_running_as_failed(
    db: None,
) -> None:
    """On startup, any pipeline with ``status="running"`` must be
    transitioned to ``"failed"``."""
    from apps.orchestrator.orchestrator_async import reap_orphaned_pipelines

    pipeline = Pipeline.objects.create(
        invocation_name="orphan-test",
        status="running",
    )

    await reap_orphaned_pipelines()

    pipeline.refresh_from_db()
    assert pipeline.status == "failed"


@pytest.mark.django_db(transaction=True)
async def test_reap_orphaned_does_not_affect_queued(
    db: None,
) -> None:
    """Pipelines with ``status="queued"`` must be left untouched."""
    from apps.orchestrator.orchestrator_async import reap_orphaned_pipelines

    pipeline = Pipeline.objects.create(
        invocation_name="queued-orphan",
        status="queued",
    )

    await reap_orphaned_pipelines()

    pipeline.refresh_from_db()
    assert pipeline.status == "queued"


# ═══════════════════════════════════════════════════════════════════════
# RED: run_pipeline — lifecycle
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_run_pipeline_starts_container(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_pipeline`` must call ``ContainerManager.start_container()``."""
    from apps.orchestrator.orchestrator_async import run_pipeline

    pipeline = Pipeline.objects.create(
        invocation_name="lifecycle-test",
        status="queued",
    )

    mock_cm = MagicMock()
    mock_cm.start_container = MagicMock(return_value="ctn-abc")
    mock_cm.wait_healthy = AsyncMock(return_value=MagicMock(spec=AsyncOpencode))
    mock_cm.stop_container = MagicMock()

    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.ContainerManager",
        lambda: mock_cm,
    )

    # Mock execute_stage to return completed for all stages
    mock_execute = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.execute_stage",
        mock_execute,
    )

    # Mock cleanup utilities
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._teardown_workspace",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._create_pr",
        MagicMock(),
    )

    await run_pipeline(pipeline)

    mock_cm.start_container.assert_called_once_with(pipeline)


@pytest.mark.django_db(transaction=True)
async def test_run_pipeline_calls_execute_stage_for_each_stage(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``run_pipeline`` must call ``execute_stage`` for each stage in
    ``STAGE_ORDER``."""
    from apps.orchestrator.orchestrator_async import run_pipeline
    from apps.orchestrator.orchestrator import STAGE_ORDER

    pipeline = Pipeline.objects.create(
        invocation_name="stages-test",
        status="running",
    )
    # Create stage records
    for name in STAGE_ORDER:
        PipelineStage.objects.create(pipeline=pipeline, name=name)

    mock_cm = MagicMock()
    mock_cm.start_container = MagicMock(return_value="ctn-abc")
    mock_cm.wait_healthy = AsyncMock(return_value=MagicMock(spec=AsyncOpencode))
    mock_cm.stop_container = MagicMock()

    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.ContainerManager",
        lambda: mock_cm,
    )

    mock_execute = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.execute_stage",
        mock_execute,
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._teardown_workspace",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._create_pr",
        MagicMock(),
    )

    await run_pipeline(pipeline)

    assert mock_cm.start_container.called
    assert mock_execute.called


@pytest.mark.django_db(transaction=True)
async def test_run_pipeline_stops_container_on_completion(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """After all stages, ``run_pipeline`` must stop the container and
    teardown the workspace."""
    from apps.orchestrator.orchestrator_async import run_pipeline

    pipeline = Pipeline.objects.create(
        invocation_name="cleanup-test",
        status="queued",
    )

    mock_cm = MagicMock()
    mock_cm.start_container = MagicMock(return_value="ctn-abc")
    mock_cm.wait_healthy = AsyncMock(return_value=MagicMock(spec=AsyncOpencode))
    mock_cm.stop_container = MagicMock()

    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.ContainerManager",
        lambda: mock_cm,
    )

    mock_execute = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.execute_stage",
        mock_execute,
    )

    mock_teardown = MagicMock()
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._teardown_workspace",
        mock_teardown,
    )
    mock_pr = MagicMock()
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._create_pr",
        mock_pr,
    )

    await run_pipeline(pipeline)

    mock_cm.stop_container.assert_called_once_with(pipeline)
    mock_teardown.assert_called_once_with(pipeline)
    mock_pr.assert_called_once_with(pipeline)


# ═══════════════════════════════════════════════════════════════════════
# RED: semaphore behavior
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_semaphore_limits_concurrent_pipelines(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """With ``MAX_ACTIVE_PIPELINES=1``, only one pipeline should run at a
    time.  A second queued pipeline stays queued until the first finishes."""
    from apps.orchestrator.orchestrator_async import run_pipeline

    pipeline1 = Pipeline.objects.create(
        invocation_name="first", status="queued",
    )
    pipeline2 = Pipeline.objects.create(
        invocation_name="second", status="queued",
    )

    # Give pipeline1 a stage so blocking_execute is actually reached
    PipelineStage.objects.create(pipeline=pipeline1, name="RED")

    # Use an event to make the first pipeline block so we can observe
    # that the second stays queued.
    block_in_stage = asyncio.Event()

    async def blocking_execute(*args, **kwargs):
        await block_in_stage.wait()

    mock_cm = MagicMock()
    mock_cm.start_container = MagicMock(return_value="ctn-abc")
    mock_cm.wait_healthy = AsyncMock(return_value=MagicMock(spec=AsyncOpencode))
    mock_cm.stop_container = MagicMock()

    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.ContainerManager",
        lambda: mock_cm,
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async.execute_stage",
        blocking_execute,
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._teardown_workspace",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator_async._create_pr",
        MagicMock(),
    )

    # Run both pipelines concurrently
    async def run_both():
        semaphore = asyncio.Semaphore(1)
        t1 = asyncio.create_task(run_pipeline(pipeline1, semaphore))
        t2 = asyncio.create_task(run_pipeline(pipeline2, semaphore))
        # Give tasks time to start
        await asyncio.sleep(0.05)
        # Pipeline1 should be running, pipeline2 queued
        pipeline1.refresh_from_db()
        pipeline2.refresh_from_db()
        assert pipeline1.status == "running"
        assert pipeline2.status == "queued"
        # Let pipeline1 finish
        block_in_stage.set()
        await asyncio.gather(t1, t2)

    await run_both()

    pipeline2.refresh_from_db()
    assert pipeline2.status != "queued"


# ═══════════════════════════════════════════════════════════════════════
# RED: handle_stage_failure
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_handle_stage_failure_increments_retry(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """``handle_stage_failure`` must increment ``retry_count`` on the stage."""
    from apps.orchestrator.orchestrator_async import handle_stage_failure

    pipeline = Pipeline.objects.create(
        invocation_name="retry-test", status="running",
    )
    stage = PipelineStage.objects.create(
        pipeline=pipeline, name="RED", status="running", retry_count=0,
    )

    await handle_stage_failure(pipeline, stage)

    assert stage.retry_count == 1


@pytest.mark.django_db(transaction=True)
async def test_handle_stage_failure_fails_pipeline_on_max_retries(
    db: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """When ``retry_count`` exceeds ``PIPELINE_MAX_RETRIES``, the pipeline
    must be marked ``"failed"``."""
    from apps.orchestrator.orchestrator_async import handle_stage_failure

    max_retries = settings.PIPELINE_MAX_RETRIES

    pipeline = Pipeline.objects.create(
        invocation_name="max-retry-test", status="running",
    )
    stage = PipelineStage.objects.create(
        pipeline=pipeline, name="RED", status="running",
        retry_count=max_retries,
    )

    await handle_stage_failure(pipeline, stage)

    assert stage.status == "failed"
    assert pipeline.status == "failed"


# ═══════════════════════════════════════════════════════════════════════
# RED: wake signal
# ═══════════════════════════════════════════════════════════════════════


def test_wake_signal_available() -> None:
    """``orchestrator_async`` must expose a ``wake_event`` that can be set
    to trigger an immediate queue check."""
    from apps.orchestrator.orchestrator_async import wake_event  # noqa: F401


def test_main_exists() -> None:
    """``orchestrator_async`` must expose a ``main`` coroutine."""
    from apps.orchestrator.orchestrator_async import main  # noqa: F401
