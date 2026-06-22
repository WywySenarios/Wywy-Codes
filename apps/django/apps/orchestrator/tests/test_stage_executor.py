"""Tests for the session-based stage executor.

Cycle 5 of the opencode-server pipeline: replace the old ``_run_stage``
polling loop with an HTTP-driven flow that creates a session, sends a
prompt, and inspects the ``MessageResponse`` parts for completion,
blocked, or failure markers.
"""

from __future__ import annotations

import enum
from typing import Any
from unittest.mock import AsyncMock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator.agent_client import AgentClient, MessageResponse
from apps.orchestrator.models import Pipeline, PipelineStage


# ═══════════════════════════════════════════════════════════════════════
# RED: module must exist
# ═══════════════════════════════════════════════════════════════════════


def test_stage_executor_module_exists() -> None:
    """The ``stage_executor`` module must exist."""
    import apps.orchestrator.stage_executor  # noqa: F401


def test_execute_stage_function_exists() -> None:
    """``stage_executor`` must expose an ``execute_stage`` function."""
    from apps.orchestrator.stage_executor import execute_stage  # noqa: F401


def test_stage_result_enum_exists() -> None:
    """``stage_executor`` must expose a ``StageResult`` enum."""
    from apps.orchestrator.stage_executor import StageResult  # noqa: F401


def test_build_stage_prompt_function_exists() -> None:
    """``stage_executor`` must expose a ``build_stage_prompt`` function."""
    from apps.orchestrator.stage_executor import build_stage_prompt  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════
# RED: StageResult enum values
# ═══════════════════════════════════════════════════════════════════════


def test_stage_result_has_expected_members() -> None:
    """``StageResult`` must have ``COMPLETED``, ``BLOCKED``, and ``FAILED``."""
    from apps.orchestrator.stage_executor import StageResult

    assert StageResult.COMPLETED in StageResult
    assert StageResult.BLOCKED in StageResult
    assert StageResult.FAILED in StageResult


# ═══════════════════════════════════════════════════════════════════════
# RED: build_stage_prompt
# ═══════════════════════════════════════════════════════════════════════


def test_build_stage_prompt_returns_list_of_parts(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """``build_stage_prompt`` must return a list of message part dicts."""
    from apps.orchestrator.stage_executor import build_stage_prompt

    stage = pipeline_running.stages.get(name="RED")
    parts = build_stage_prompt(pipeline_running, stage)

    assert isinstance(parts, list)
    assert len(parts) >= 1
    for part in parts:
        assert isinstance(part, dict)
        assert "type" in part
        assert "text" in part or "content" in part


def test_build_stage_prompt_includes_stage_name(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """The prompt must include the current stage name."""
    from apps.orchestrator.stage_executor import build_stage_prompt

    stage = pipeline_running.stages.get(name="RED")
    parts = build_stage_prompt(pipeline_running, stage)

    text = " ".join(
        part.get("text", part.get("content", "")) for part in parts
    ).lower()
    assert "red" in text or stage.name in text


def test_build_stage_prompt_includes_pipeline_context(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """The prompt must include pipeline-level context (name, status)."""
    from apps.orchestrator.stage_executor import build_stage_prompt

    stage = pipeline_running.stages.get(name="RED")
    parts = build_stage_prompt(pipeline_running, stage)

    text = " ".join(
        part.get("text", part.get("content", "")) for part in parts
    )
    assert pipeline_running.invocation_name in text or "running" in text.lower()


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — helper to build a mock AgentClient
# ═══════════════════════════════════════════════════════════════════════


def _make_mock_client(parts: list[dict[str, Any]]) -> AsyncMock:
    """Build a mock ``AgentClient`` whose ``send_message`` returns *parts*."""
    client = AsyncMock(spec=AgentClient)
    client.create_session = AsyncMock(return_value="session-red-001")
    client.send_message = AsyncMock(
        return_value=MessageResponse(id="msg-001", parts=parts)
    )
    return client


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — completed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_creates_session(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """``execute_stage`` must create a session on the opencode server."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "Stage RED completed successfully"}]
    )

    result = await execute_stage(pipeline_running, stage, client)

    client.create_session.assert_awaited_once()
    # The session title should include the stage name
    call_kwargs = client.create_session.call_args
    title = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("title", "")
    assert "red" in title.lower() or stage.name in title


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_sends_message(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """``execute_stage`` must send a message with prompt parts."""
    from apps.orchestrator.stage_executor import execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "completed"}]
    )

    await execute_stage(pipeline_running, stage, client)

    client.send_message.assert_awaited_once()
    # Verify session_id from create_session is used
    call_kwargs = client.send_message.call_args
    args, kwargs = call_kwargs
    assert len(args) >= 1 or "session_id" in kwargs


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_saves_session_id_on_stage(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """The ``session_id`` returned by the server must be persisted on the
    ``PipelineStage`` record."""
    from apps.orchestrator.stage_executor import execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "completed"}]
    )

    await execute_stage(pipeline_running, stage, client)

    assert stage.session_id is not None
    assert stage.session_id == "session-red-001"


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_marks_stage_completed_on_success(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """When the server responds with a completion marker, ``execute_stage``
    must set ``stage.status = "completed"`` and return ``StageResult.COMPLETED``."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "Stage completed"}]
    )

    result = await execute_stage(pipeline_running, stage, client)

    assert stage.status == "completed"
    assert result == StageResult.COMPLETED


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_sets_started_and_finished_at(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """``execute_stage`` must set ``started_at`` and ``finished_at`` timestamps."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "completed"}]
    )

    await execute_stage(pipeline_running, stage, client)

    assert stage.started_at is not None
    assert stage.finished_at is not None
    assert stage.finished_at >= stage.started_at


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — blocked
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_marks_stage_blocked(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """When the server responds with a blocked marker, ``execute_stage`` must
    set ``stage.status = "blocked"`` and ``pipeline.user_input_pending = True``."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "input_required", "text": "Which approach should I use?"}]
    )

    result = await execute_stage(pipeline_running, stage, client)

    assert stage.status == "blocked"
    assert pipeline_running.user_input_pending is True
    assert result == StageResult.BLOCKED


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — failed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_marks_stage_failed_on_client_error(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """When the ``AgentClient`` raises ``AgentClientError``, ``execute_stage`` must
    set ``stage.status = "failed"`` and return ``StageResult.FAILED``."""
    from apps.orchestrator.agent_client import AgentClientError
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = AsyncMock(spec=AgentClient)
    client.create_session = AsyncMock(return_value="session-fail-001")
    client.send_message = AsyncMock(side_effect=AgentClientError("Connection refused"))

    result = await execute_stage(pipeline_running, stage, client)

    assert stage.status == "failed"
    assert result == StageResult.FAILED


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_marks_stage_failed_on_error_part(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """When the server response contains an error part, ``execute_stage`` must
    set ``stage.status = "failed"`` and return ``StageResult.FAILED``."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "error", "text": "Agent encountered an error"}]
    )

    result = await execute_stage(pipeline_running, stage, client)

    assert stage.status == "failed"
    assert result == StageResult.FAILED


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — retry tracking
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_clears_retry_after(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """On a successful execution, ``execute_stage`` must clear ``retry_after``."""
    from django.utils import timezone
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    stage.retry_after = timezone.now() + timezone.timedelta(seconds=30)
    stage.retry_count = 1

    client = _make_mock_client(
        parts=[{"type": "text", "text": "completed"}]
    )

    await execute_stage(pipeline_running, stage, client)

    assert stage.retry_after is None
