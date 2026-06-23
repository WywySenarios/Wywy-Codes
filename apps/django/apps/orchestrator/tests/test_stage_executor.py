"""Tests for the session-based stage executor.

Cycle 5 of the opencode-server pipeline: replace the old ``_run_stage``
polling loop with an HTTP-driven flow that creates a session, sends a
prompt, and inspects the ``MessageResponse`` parts for completion,
blocked, or failure markers.
"""

from __future__ import annotations

import enum
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

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


def _make_mock_client(parts: list[Any]) -> AsyncMock:
    """Build a mock ``AsyncOpencode`` whose ``session.messages`` returns
    *parts*."""
    client = AsyncMock()
    client.session.create = AsyncMock(
        return_value=MagicMock(id="session-red-001"),
    )
    client.session.chat = AsyncMock(
        return_value=MagicMock(id="msg-001", error=None),
    )
    client.session.messages = AsyncMock(
        return_value=[MagicMock(
            info=MagicMock(id="msg-001"),
            parts=parts,
        )],
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

    client.session.create.assert_awaited_once()


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

    client.session.chat.assert_awaited_once_with(
        "session-red-001",
        model_id=ANY,
        provider_id=ANY,
        parts=ANY,
    )


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
    """When the server returns a ``ToolPart`` with pending status,
    ``execute_stage`` must set ``stage.status = "blocked"`` and
    ``pipeline.user_input_pending = True``."""
    from opencode_ai.types import ToolPart
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    blocked_part = ToolPart(
        type="tool",
        id="block-1",
        callID="call-1",
        messageID="msg-001",
        sessionID="session-red-001",
        state={"status": "pending"},
        tool="some_tool",
    )
    client = _make_mock_client(parts=[blocked_part])

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
    """When the ``AsyncOpencode`` raises ``APIStatusError``,
    ``execute_stage`` must set ``stage.status = "failed"`` and return
    ``StageResult.FAILED``."""
    from opencode_ai import APIStatusError
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = AsyncMock()
    client.session.create = AsyncMock(
        side_effect=APIStatusError(
            "Connection refused",
            response=MagicMock(status_code=503),
            body=None,
        ),
    )

    result = await execute_stage(pipeline_running, stage, client)

    assert stage.status == "failed"
    assert result == StageResult.FAILED


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_marks_stage_failed_on_error(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """When ``chat()`` returns a response with an error, ``execute_stage``
    must set ``stage.status = "failed"`` and return ``StageResult.FAILED``."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = AsyncMock()
    client.session.create = AsyncMock(
        return_value=MagicMock(id="session-fail-001"),
    )
    client.session.chat = AsyncMock(
        return_value=MagicMock(id="msg-001", error="Provider error"),
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


# ═══════════════════════════════════════════════════════════════════════
# RED: execute_stage — two-step flow
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db(transaction=True)
async def test_execute_stage_calls_messages_after_chat(
    db: None,
    pipeline_running: Pipeline,
) -> None:
    """After ``chat()`` succeeds (no error), ``execute_stage`` must call
    ``session.messages()`` to retrieve the response parts."""
    from apps.orchestrator.stage_executor import StageResult, execute_stage

    stage = pipeline_running.stages.get(name="RED")
    client = _make_mock_client(
        parts=[{"type": "text", "text": "completed"}]
    )

    result = await execute_stage(pipeline_running, stage, client)

    client.session.messages.assert_awaited_once_with("session-red-001")
    assert result == StageResult.COMPLETED


# ═══════════════════════════════════════════════════════════════════════
# Cycle 6: is_blocked imported from new home in stage_executor
# ═══════════════════════════════════════════════════════════════════════


async def test_is_blocked_imported_from_stage_executor_returns_true_when_pending() -> None:
    """``is_blocked`` must be importable from ``apps.orchestrator.stage_executor``
    and return ``True`` when any ``ToolPart`` has ``state.status == "pending"``."""
    from apps.orchestrator.stage_executor import is_blocked
    from opencode_ai.types import ToolPart, ToolStatePending

    parts = [
        ToolPart(
            id="p1", callID="c1", messageID="m1", sessionID="s1",
            tool="bash", type="tool",
            state=ToolStatePending(status="pending"),
        ),
    ]
    assert is_blocked(parts) is True


async def test_is_blocked_imported_from_stage_executor_returns_false_when_completed() -> None:
    """``is_blocked`` must return ``False`` when no ``ToolPart`` has pending
    status."""
    from apps.orchestrator.stage_executor import is_blocked
    from opencode_ai.types import ToolPart, ToolStateCompleted

    parts = [
        ToolPart(
            id="p1", callID="c1", messageID="m1", sessionID="s1",
            tool="bash", type="tool",
            state=ToolStateCompleted(
                status="completed", input={}, metadata={},
                output="done", time={"start": 0.0, "end": 1.0}, title="x",
            ),
        ),
    ]
    assert is_blocked(parts) is False


# ═══════════════════════════════════════════════════════════════════════
# Cycle 6: has_error imported from new home in stage_executor
# ═══════════════════════════════════════════════════════════════════════


async def test_has_error_imported_from_stage_executor_returns_true_when_error_set() -> None:
    """``has_error`` must be importable from ``apps.orchestrator.stage_executor``
    and return ``True`` when ``AssistantMessage.error`` is not ``None``."""
    from apps.orchestrator.stage_executor import has_error
    from opencode_ai.types import AssistantMessage
    from opencode_ai.types.shared import UnknownError

    msg = AssistantMessage(
        id="m1", sessionID="s1", role="assistant",
        modelID="deepseek/deepseek-chat", providerID="deepseek",
        cost=0.0, mode="agent",
        path={"cwd": "/", "root": "/"},
        time={"created": 0.0, "completed": 0.0},
        tokens={"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        system=[],
        error=UnknownError(data={"message": "fail"}, name="UnknownError"),
    )
    assert has_error(msg) is True


async def test_has_error_imported_from_stage_executor_returns_false_when_no_error() -> None:
    """``has_error`` must return ``False`` when ``AssistantMessage.error``
    is ``None``."""
    from apps.orchestrator.stage_executor import has_error
    from opencode_ai.types import AssistantMessage

    msg = AssistantMessage(
        id="m1", sessionID="s1", role="assistant",
        modelID="deepseek/deepseek-chat", providerID="deepseek",
        cost=0.0, mode="agent",
        path={"cwd": "/", "root": "/"},
        time={"created": 0.0, "completed": 0.0},
        tokens={"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        system=[],
        error=None,
    )
    assert has_error(msg) is False
