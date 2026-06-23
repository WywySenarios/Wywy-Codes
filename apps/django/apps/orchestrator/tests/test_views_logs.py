"""Tests for GET /api/pipelines/<uuid:id>/logs/<str:stage_name>/ endpoint.

Returns structured log entries from session messages (when the stage has a
``session_id``) or file-based logs, always merged with orchestrator log
entries and sorted by timestamp.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestApiStageLogs:
    """Tests for the stage-aware structured log endpoint."""

    @staticmethod
    def url(pipeline_id: str, stage_name: str) -> str:
        return f"/api/pipelines/{pipeline_id}/logs/{stage_name}/"

    # ------------------------------------------------------------------ #
    #  Session-based (stage has session_id)
    # ------------------------------------------------------------------ #

    def test_session_messages_returned_as_typed_entries(
        self, client, db, pipeline_blocked_with_session,
    ) -> None:
        """When stage has ``session_id``, returns typed entries from session
        messages with ``type`` and ``content`` fields."""
        pipeline = pipeline_blocked_with_session
        with patch("apps.orchestrator.views.AsyncOpencode", create=True) as MockAC:
            mock_client = MockAC.return_value
            mock_client.session.messages = AsyncMock(
                return_value=[
                    MagicMock(parts=[
                        {"type": "text", "text": "Hello from agent"},
                    ]),
                    MagicMock(parts=[
                        {"type": "tool_use", "name": "bash",
                         "input": {"command": "echo hi"}},
                    ]),
                    MagicMock(parts=[
                        {"type": "tool_result", "content": "hi\n"},
                    ]),
                ],
            )
            response = client.get(self.url(pipeline.id, "GREEN"))

        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        entries = data["entries"]
        assert len(entries) == 3

        # All three part types appear
        types = {e["type"] for e in entries}
        assert "text" in types
        assert "tool_use" in types
        assert "tool_result" in types

        # Every entry has ts and content
        for e in entries:
            assert "ts" in e
            assert isinstance(e["ts"], str)
            assert "content" in e

        # Content is preserved
        for e in entries:
            if e["type"] == "text":
                assert e["content"] == "Hello from agent"
            elif e["type"] == "tool_use":
                # tool_use content includes the tool input
                assert "command" in str(e["content"]) or "echo hi" in str(e["content"])
            elif e["type"] == "tool_result":
                assert e["content"] == "hi\n"

    def test_session_and_orchestrator_logs_merged(
        self, client, db, pipeline_blocked_with_session, temp_log_root,
    ) -> None:
        """Orchestrator log entries are merged with session-derived entries."""
        pipeline = pipeline_blocked_with_session

        # Create orchestrator.log
        log_dir = temp_log_root / str(pipeline.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "orchestrator.log").write_text(
            json.dumps({
                "ts": "2026-01-01T00:00:00Z",
                "level": "INFO",
                "msg": "orchestrator event",
                "src": "orchestrator",
            }) + "\n"
        )

        with patch("apps.orchestrator.views.AsyncOpencode", create=True) as MockAC:
            mock_client = MockAC.return_value
            mock_client.session.messages = AsyncMock(
                return_value=[
                    MagicMock(parts=[
                        {"type": "text", "text": "agent message"},
                    ]),
                ],
            )
            response = client.get(self.url(pipeline.id, "GREEN"))

        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) > 1

        types = {e["type"] for e in entries}
        assert "text" in types
        assert "orchestrator" in types

    # ------------------------------------------------------------------ #
    #  File-based fallback (no session_id)
    # ------------------------------------------------------------------ #

    def test_fallback_to_file_logs_when_no_session_id(
        self, client, db, pipeline_running, temp_log_root,
    ) -> None:
        """When stage has no ``session_id``, falls back to ``{stage}.log`` file."""
        pipeline = pipeline_running
        log_dir = temp_log_root / str(pipeline.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "RED.log").write_text(
            json.dumps({"ts": "2026-01-01T00:00:00Z", "msg": "stage started"}) + "\n"
        )

        response = client.get(self.url(pipeline.id, "RED"))
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["msg"] == "stage started"

    def test_fallback_merges_orchestrator_log(
        self, client, db, pipeline_running, temp_log_root,
    ) -> None:
        """File-based fallback also merges ``orchestrator.log`` entries."""
        pipeline = pipeline_running
        log_dir = temp_log_root / str(pipeline.id)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Stage log
        (log_dir / "RED.log").write_text(
            json.dumps({"ts": "2026-01-01T00:00:01Z", "msg": "stage"}) + "\n"
        )
        # Orchestrator log
        (log_dir / "orchestrator.log").write_text(
            json.dumps({
                "ts": "2026-01-01T00:00:00Z",
                "msg": "orch",
                "src": "orchestrator",
            }) + "\n"
        )

        response = client.get(self.url(pipeline.id, "RED"))
        entries = response.json()["entries"]
        assert len(entries) == 2

    # ------------------------------------------------------------------ #
    #  Filtering
    # ------------------------------------------------------------------ #

    def test_lines_parameter_limits_entries(
        self, client, db, pipeline_blocked_with_session,
    ) -> None:
        """``?lines=N`` limits returned entries to the last N."""
        pipeline = pipeline_blocked_with_session
        with patch("apps.orchestrator.views.AsyncOpencode", create=True) as MockAC:
            mock_client = MockAC.return_value
            mock_client.session.messages = AsyncMock(
                return_value=[
                    MagicMock(parts=[
                        {"type": "text", "text": f"msg {i}"},
                    ])
                    for i in range(10)
                ],
            )
            response = client.get(
                self.url(pipeline.id, "GREEN") + "?lines=3"
            )

        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) == 3

    # ------------------------------------------------------------------ #
    #  Error cases
    # ------------------------------------------------------------------ #

    def test_404_for_nonexistent_pipeline(self, client, db) -> None:
        response = client.get(
            "/api/pipelines/"
            "00000000-0000-0000-0000-000000000000/logs/RED/"
        )
        assert response.status_code == 404

    def test_404_for_nonexistent_stage(
        self, client, db, pipeline_running,
    ) -> None:
        """Valid pipeline but stage name does not match any stage."""
        pipeline = pipeline_running
        response = client.get(self.url(pipeline.id, "NONEXISTENT"))
        assert response.status_code == 404

    def test_rejects_non_get_methods(
        self, client, db, pipeline_running,
    ) -> None:
        response = client.post(
            f"/api/pipelines/{pipeline_running.id}/logs/RED/"
        )
        assert response.status_code == 405

    def test_empty_entries_when_no_session_and_no_log_files(
        self, client, db, pipeline_running, temp_log_root,
    ) -> None:
        """When no session_id and no log files exist, returns empty list."""
        pipeline = pipeline_running
        response = client.get(self.url(pipeline.id, "RED"))
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_invalid_lines_parameter_returns_400(
        self, client, db, pipeline_running,
    ) -> None:
        pipeline = pipeline_running
        response = client.get(
            self.url(pipeline.id, "RED") + "?lines=notanumber"
        )
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# Cycle 6: part_to_log_entry imported from new home in views
# ═══════════════════════════════════════════════════════════════════════


def test_part_to_log_entry_imported_from_views_text_part() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``TextPart`` to a log entry with ``type="text"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import TextPart

    part = TextPart(id="p1", messageID="m1", sessionID="s1", text="hello", type="text")
    entry = part_to_log_entry(part)

    assert entry["type"] == "text"
    assert entry["content"] == "hello"
    assert "ts" in entry


def test_part_to_log_entry_imported_from_views_tool_part() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``ToolPart`` to a log entry with ``type="tool_use"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import ToolPart, ToolStateCompleted

    state = ToolStateCompleted(
        status="completed", input={"cmd": "ls"}, metadata={},
        output="files", time={"start": 0.0, "end": 1.0}, title="List",
    )
    part = ToolPart(
        id="p2", callID="c1", messageID="m1", sessionID="s1",
        tool="bash", type="tool", state=state,
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "tool_use"
    assert "content" in entry


def test_part_to_log_entry_imported_from_views_step_start() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``StepStartPart`` to a log entry with ``type="step_start"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import StepStartPart

    part = StepStartPart(id="p3", messageID="m1", sessionID="s1", type="step-start")
    entry = part_to_log_entry(part)

    assert entry["type"] == "step_start"


def test_part_to_log_entry_imported_from_views_step_finish() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``StepFinishPart`` to a log entry with ``type="step_finish"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import StepFinishPart

    part = StepFinishPart(
        id="p4", messageID="m1", sessionID="s1", type="step-finish",
        cost=0.0,
        tokens={"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    )
    entry = part_to_log_entry(part)

    assert entry["type"] == "step_finish"


def test_part_to_log_entry_imported_from_views_file_part() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``FilePart`` to a log entry with ``type="file"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import FilePart

    part = FilePart(id="p5", messageID="m1", sessionID="s1", type="file", mime="text/plain", url="/tmp/f.txt")
    entry = part_to_log_entry(part)

    assert entry["type"] == "file"


def test_part_to_log_entry_imported_from_views_snapshot_part() -> None:
    """``part_to_log_entry`` must be importable from ``apps.orchestrator.views``
    and convert a ``SnapshotPart`` to a log entry with ``type="snapshot"``."""
    from apps.orchestrator.views import part_to_log_entry
    from opencode_ai.types import SnapshotPart

    part = SnapshotPart(id="p6", messageID="m1", sessionID="s1", type="snapshot", snapshot="content")
    entry = part_to_log_entry(part)

    assert entry["type"] == "snapshot"
