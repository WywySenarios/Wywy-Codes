"""Tests for the opencode server agent adapter.

After migrating to the official ``opencode_ai`` SDK, ``agent_client.py``
is a thin adapter that provides typed helper functions on top of SDK models:

- ``part_to_log_entry(part)`` — converts a typed ``Part`` to a log dict
- ``is_blocked(parts)`` — checks if any ``ToolPart`` has pending status
- ``has_error(message)`` — checks if an ``AssistantMessage`` has an error
- ``AgentClientError`` — kept for backward compatibility
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from opencode_ai.types import (
    AssistantMessage,
    FilePart,
    Part,
    SnapshotPart,
    StepFinishPart,
    StepStartPart,
    TextPart,
    ToolPart,
    ToolStateCompleted,
    ToolStatePending,
)
from opencode_ai.types.shared import UnknownError

# ── adapter existence ──────────────────────────────────────────────────


async def test_agent_client_module_exists() -> None:
    """The ``agent_client`` module must exist under
    ``apps.orchestrator.agent_client``."""
    import apps.orchestrator.agent_client  # noqa: F401


async def test_agent_client_error_exists() -> None:
    """The module must expose the ``AgentClientError`` exception class for
    backward compatibility."""
    from apps.orchestrator.agent_client import AgentClientError  # noqa: F401


# ── part_to_log_entry ──────────────────────────────────────────────────


def _ts_approx() -> str:
    """Return an ISO timestamp for today (approximately matches
    ``datetime.now(timezone.utc).isoformat()``)."""
    return datetime.now(timezone.utc).isoformat()[:10]


async def test_part_to_log_entry_text_part() -> None:
    """``part_to_log_entry`` must convert a ``TextPart`` to a log entry
    with ``type="text"`` and ``content`` set to the part's text."""
    from apps.orchestrator.agent_client import part_to_log_entry

    part = TextPart(
        id="p1",
        messageID="m1",
        sessionID="s1",
        text="Hello, world!",
        type="text",
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "text"
    assert entry["content"] == "Hello, world!"
    assert "ts" in entry


async def test_part_to_log_entry_tool_part() -> None:
    """``part_to_log_entry`` must convert a ``ToolPart`` to a log entry
    with ``type="tool_use"`` and ``content`` containing the tool name and
    input."""
    from apps.orchestrator.agent_client import part_to_log_entry

    state = ToolStateCompleted(
        status="completed",
        input={"command": "ls -la"},
        metadata={},
        output="file1.txt\nfile2.txt",
        time={"start": 100.0, "end": 101.0},
        title="List files",
    )
    part = ToolPart(
        id="p2",
        callID="call_001",
        messageID="m1",
        sessionID="s1",
        tool="bash",
        state=state,
        type="tool",
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "tool_use"
    assert "content" in entry


async def test_part_to_log_entry_step_start() -> None:
    """``part_to_log_entry`` must convert a ``StepStartPart`` to a log entry
    with ``type="step_start"``."""
    from apps.orchestrator.agent_client import part_to_log_entry

    part = StepStartPart(id="p3", messageID="m1", sessionID="s1", type="step-start")
    entry = part_to_log_entry(part)

    assert entry["type"] == "step_start"


async def test_part_to_log_entry_step_finish() -> None:
    """``part_to_log_entry`` must convert a ``StepFinishPart`` to a log entry
    with ``type="step_finish"``."""
    from apps.orchestrator.agent_client import part_to_log_entry

    part = StepFinishPart(
        id="p4",
        messageID="m1",
        sessionID="s1",
        type="step-finish",
        cost=0.05,
        tokens={"input": 100, "output": 50, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "step_finish"


async def test_part_to_log_entry_file_part() -> None:
    """``part_to_log_entry`` must convert a ``FilePart`` to a log entry
    with ``type="file"``."""
    from apps.orchestrator.agent_client import part_to_log_entry

    part = FilePart(
        id="p5",
        messageID="m1",
        sessionID="s1",
        type="file",
        mime="text/plain",
        url="file:///tmp/test.txt",
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "file"


async def test_part_to_log_entry_snapshot_part() -> None:
    """``part_to_log_entry`` must convert a ``SnapshotPart`` to a log entry
    with ``type="snapshot"``."""
    from apps.orchestrator.agent_client import part_to_log_entry

    part = SnapshotPart(
        id="p6",
        messageID="m1",
        sessionID="s1",
        type="snapshot",
        snapshot="file content here",
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "snapshot"


# ── is_blocked ─────────────────────────────────────────────────────────


async def test_is_blocked_returns_true_when_tool_part_pending() -> None:
    """``is_blocked`` must return ``True`` when any part is a ``ToolPart``
    with ``state.status == "pending"``."""
    from apps.orchestrator.agent_client import is_blocked

    pending_state = ToolStatePending(status="pending")
    parts: list[Part] = [
        TextPart(id="p1", messageID="m1", sessionID="s1", text="thinking", type="text"),
        ToolPart(
            id="p2",
            callID="call_001",
            messageID="m1",
            sessionID="s1",
            tool="bash",
            state=pending_state,
            type="tool",
        ),
    ]

    assert is_blocked(parts) is True


async def test_is_blocked_returns_false_when_all_completed() -> None:
    """``is_blocked`` must return ``False`` when no ``ToolPart`` has
    pending status."""
    from apps.orchestrator.agent_client import is_blocked

    completed_state = ToolStateCompleted(
        status="completed",
        input={},
        metadata={},
        output="done",
        time={"start": 100.0, "end": 101.0},
        title="Task",
    )
    parts: list[Part] = [
        TextPart(id="p1", messageID="m1", sessionID="s1", text="result", type="text"),
        ToolPart(
            id="p2",
            callID="call_001",
            messageID="m1",
            sessionID="s1",
            tool="bash",
            state=completed_state,
            type="tool",
        ),
    ]

    assert is_blocked(parts) is False


async def test_is_blocked_returns_false_for_empty_parts() -> None:
    """``is_blocked`` must return ``False`` when given an empty list."""
    from apps.orchestrator.agent_client import is_blocked

    assert is_blocked([]) is False


# ── has_error ──────────────────────────────────────────────────────────


async def test_has_error_returns_true_when_error_set() -> None:
    """``has_error`` must return ``True`` when the ``AssistantMessage``
    has a non-``None`` error field."""
    from apps.orchestrator.agent_client import has_error

    error = UnknownError(data={"message": "API unavailable"}, name="UnknownError")
    msg = AssistantMessage(
        id="m1",
        sessionID="s1",
        modelID="deepseek/deepseek-chat",
        providerID="deepseek",
        role="assistant",
        cost=0.01,
        mode="agent",
        path={"cwd": "/workspace", "root": "/workspace"},
        time={"created": 1000.0, "completed": 1001.0},
        tokens={"input": 10, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        system=[],
        error=error,
    )

    assert has_error(msg) is True


async def test_has_error_returns_false_when_no_error() -> None:
    """``has_error`` must return ``False`` when the ``AssistantMessage``
    has ``error=None``."""
    from apps.orchestrator.agent_client import has_error

    msg = AssistantMessage(
        id="m1",
        sessionID="s1",
        modelID="deepseek/deepseek-chat",
        providerID="deepseek",
        role="assistant",
        cost=0.01,
        mode="agent",
        path={"cwd": "/workspace", "root": "/workspace"},
        time={"created": 1000.0, "completed": 1001.0},
        tokens={"input": 10, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        system=[],
        error=None,
    )

    assert has_error(msg) is False
