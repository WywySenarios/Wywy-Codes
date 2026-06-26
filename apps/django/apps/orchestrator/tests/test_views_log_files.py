"""Tests for log file listing and per-file log entry endpoints.

These tests cover:
  - GET  /api/pipelines/<uuid:id>/logs/              — list available log files
  - GET  /api/pipelines/<uuid:id>/logs/entries/<str:filename>/   — JSON entries, single file
  - GET  /api/pipelines/<uuid:id>/logs/entries/<str:filename>/?raw — raw text dump
"""

from __future__ import annotations

import json
from pathlib import Path


# ── Shared log-file creation helpers ──────────────────────────────────── #


def create_pipeline_log(
    log_root: Path, pipeline_id: str, filename: str,
    entries: list[dict] | None = None,
) -> Path:
    """Create a log file in a pipeline's log directory.

    If *entries* is ``None``, creates an empty file (useful for listing tests
    that only need the file to exist).
    """
    log_dir = log_root / str(pipeline_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / filename
    if entries is not None:
        lines = "\n".join(json.dumps(e) for e in entries) + "\n"
        log_file.write_text(lines)
    else:
        log_file.touch()
    return log_file


def create_log_file(log_root: Path, filename: str, entries: list[dict]) -> Path:
    """Write *entries* as JSON Lines to a file at *log_root* root."""
    log_file = log_root / filename
    lines = "\n".join(json.dumps(e) for e in entries) + "\n"
    log_file.write_text(lines)
    return log_file


class TestLogFilesList:
    """GET /api/pipelines/<id>/logs/ — list available log filenames."""

    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/logs/"

    # ------------------------------------------------------------------ #
    #  Tests – list
    # ------------------------------------------------------------------ #

    def test_list_returns_available_filenames(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log")
        create_pipeline_log(temp_log_root, pipeline_queued.id, "RED.log")

        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert sorted(data["logs"]) == ["RED.log", "orchestrator.log"]

    def test_list_empty_when_no_log_files(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        assert response.json()["logs"] == []

    def test_list_excludes_dotfiles(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log")
        create_pipeline_log(temp_log_root, pipeline_queued.id, ".hidden.log")

        response = client.get(self.url(pipeline_queued))
        data = response.json()
        assert "orchestrator.log" in data["logs"]
        assert ".hidden.log" not in data["logs"]

    def test_list_404_for_nonexistent_pipeline(
        self, client, db, temp_log_root,
    ):
        response = client.get(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/logs/",
        )
        assert response.status_code == 404

    def test_list_rejects_non_get_methods(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.post(self.url(pipeline_queued))
        assert response.status_code == 405


class TestLogFilesEntries:
    """GET /api/pipelines/<id>/logs/entries/<filename>/ — JSON entries."""

    @staticmethod
    def url(pipeline, filename: str) -> str:
        return f"/api/pipelines/{pipeline.id}/logs/entries/{filename}/"

    # ------------------------------------------------------------------ #
    #  Tests – entries
    # ------------------------------------------------------------------ #

    def test_returns_entries_from_requested_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        entries = [
            {"ts": "2026-01-01T00:00:00.000Z", "level": "INFO", "msg": "start"},
            {"ts": "2026-01-01T00:00:01.000Z", "level": "ERROR", "msg": "fail"},
        ]
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log", entries)

        response = client.get(self.url(pipeline_queued, "orchestrator.log"))
        assert response.status_code == 200
        result = response.json()["entries"]
        assert len(result) == 2
        assert result[0]["msg"] == "start"
        assert result[1]["msg"] == "fail"

    def test_does_not_merge_multiple_files(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Only the requested file's entries are returned, no merging."""
        create_pipeline_log(temp_log_root, pipeline_queued.id, "RED.log", [{"msg": "from stage"}])
        create_pipeline_log(
            temp_log_root, pipeline_queued.id, "orchestrator.log", [{"msg": "from orch"}],
        )

        response = client.get(self.url(pipeline_queued, "RED.log"))
        result = response.json()["entries"]
        assert len(result) == 1
        assert result[0]["msg"] == "from stage"

    def test_returns_empty_array_for_missing_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.get(self.url(pipeline_queued, "nonexistent.log"))
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_skips_invalid_json_lines(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "server.log").write_text(
            '{"ts":"t","level":"INFO","msg":"valid"}\n'
            "not valid json\n"
            '{"ts":"t","level":"ERROR","msg":"also valid"}\n'
        )

        response = client.get(self.url(pipeline_queued, "server.log"))
        result = response.json()["entries"]
        assert len(result) == 2
        assert result[0]["msg"] == "valid"
        assert result[1]["msg"] == "also valid"

    def test_returns_entries_for_json_array_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Stage logs are written as pretty-printed JSON arrays (not JSON
        Lines).  _parse_log_entries must fall back to whole-file JSON
        parsing so these entries are not silently dropped."""
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        messages = [
            {"role": "user", "content": "write a test"},
            {"role": "assistant", "content": "here it is"},
        ]
        (log_dir / "RED.log").write_text(json.dumps(messages, indent=2))

        response = client.get(self.url(pipeline_queued, "RED.log"))
        assert response.status_code == 200
        result = response.json()["entries"]
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "write a test"
        assert result[1]["role"] == "assistant"

    def test_respects_lines_parameter(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        entries = [{"msg": f"line{i}"} for i in range(10)]
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log", entries)

        response = client.get(f"{self.url(pipeline_queued, 'orchestrator.log')}?lines=3")
        result = response.json()["entries"]
        assert len(result) == 3
        assert result[0]["msg"] == "line7"
        assert result[2]["msg"] == "line9"

    def test_404_for_nonexistent_pipeline(
        self, client, db, temp_log_root,
    ):
        response = client.get(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/logs/entries/orchestrator.log/",
        )
        assert response.status_code == 404

    def test_rejects_non_get_methods(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.post(self.url(pipeline_queued, "orchestrator.log"))
        assert response.status_code == 405

    # ── Opencode message transformation ──────────────────────────────

    def test_transforms_opencode_messages_into_readable_entries(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Stage logs written as opencode session message objects (with
        ``info`` and ``parts`` keys) must be transformed into entries with
        ``ts``, ``level``, and ``msg`` fields that the LogViewer can render.

        Without this transformation the LogViewer shows blank entries because
        the raw message objects lack the expected ``ts``/``level``/``msg`` keys.
        """
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Simulate the format produced by _write_log_file in orchestrator.py
        messages = [
            {
                "info": {
                    "id": "msg_f017c7d4a001UYnZapU7Vit8tN",
                    "sessionID": "ses_0fe838536ffeUpxkt30zIfdb9h",
                    "role": "user",
                    "time": {"created": 1782436363594},
                },
                "parts": [
                    {
                        "id": "prt_f017c7d50001oEiffypLWEIJAL",
                        "type": "text",
                        "text": "Stage: init. Write to /workspace/state/state.json to report your progress.",
                    },
                ],
            },
            {
                "info": {
                    "id": "msg_f017c7d65001a3qbUNWVHUizWR",
                    "sessionID": "ses_0fe838536ffeUpxkt30zIfdb9h",
                    "role": "assistant",
                    "time": {"created": 1782436363621, "completed": 1782436365791},
                    "model": {"providerID": "opencode", "modelID": "big-pickle"},
                },
                "parts": [
                    {
                        "id": "prt_f017c82c80017F3TACSdDc6R7c",
                        "type": "reasoning",
                        "text": "The user wants me to write a state file to report progress.",
                    },
                    {
                        "id": "prt_f017c84400013rU4dnwJW2nA8a",
                        "type": "text",
                        "text": "The init stage has been completed successfully.",
                    },
                ],
            },
        ]
        (log_dir / "init.log").write_text(json.dumps(messages, indent=2))

        response = client.get(self.url(pipeline_queued, "init.log"))
        assert response.status_code == 200
        entries = response.json()["entries"]

        # Must have at least one entry per message
        assert len(entries) >= 2, (
            f"Expected at least 2 entries, got {len(entries)}. "
            "Hint: opencode message objects must be transformed into "
            "LogEntry-compatible dicts with ts/level/msg keys."
        )

        # Each entry must have the three fields the LogViewer depends on
        for i, entry in enumerate(entries):
            assert "ts" in entry, f"Entry {i} missing 'ts': {entry}"
            assert "level" in entry, f"Entry {i} missing 'level': {entry}"
            assert "msg" in entry, f"Entry {i} missing 'msg': {entry}"

        # First entry = user message
        assert entries[0]["level"] == "USER", (
            f"Expected level='USER' for user message, got '{entries[0]['level']}'"
        )
        assert "Stage: init" in entries[0]["msg"], (
            f"User message should contain the stage prompt, got: {entries[0]['msg']}"
        )

        # Second entry = agent response
        assert entries[1]["level"] == "AGENT", (
            f"Expected level='AGENT' for assistant message, got '{entries[1]['level']}'"
        )
        assert "completed successfully" in entries[1]["msg"], (
            f"Agent message should contain the response text, got: {entries[1]['msg']}"
        )

        # Timestamps should be ISO-format strings
        assert "T" in entries[0]["ts"], (
            f"Expected ISO timestamp, got: {entries[0]['ts']}"
        )


class TestLogFilesRaw:
    """GET /api/pipelines/<id>/logs/entries/<filename>/?raw — raw text dump."""

    @staticmethod
    def raw_url(pipeline, filename: str) -> str:
        return f"/api/pipelines/{pipeline.id}/logs/entries/{filename}/?raw"

    # ------------------------------------------------------------------ #
    #  Tests – raw
    # ------------------------------------------------------------------ #

    def test_raw_returns_text_content(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        content = '{"msg": "line1"}\n{"msg": "line2"}\n'
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "orchestrator.log").write_text(content)

        response = client.get(self.raw_url(pipeline_queued, "orchestrator.log"))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content.decode() == content

    def test_raw_preserves_entire_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        content = "line one\nline two\nline three\n"
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "server.log").write_text(content)

        response = client.get(self.raw_url(pipeline_queued, "server.log"))
        assert response.status_code == 200
        assert response.content.decode() == content

    def test_raw_returns_empty_for_empty_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "empty.log").write_text("")

        response = client.get(self.raw_url(pipeline_queued, "empty.log"))
        assert response.status_code == 200
        assert response.content.decode() == ""

    def test_raw_404_for_missing_file(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.get(self.raw_url(pipeline_queued, "nonexistent.log"))
        assert response.status_code == 404

    def test_raw_404_for_nonexistent_pipeline(
        self, client, db, temp_log_root,
    ):
        response = client.get(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/logs/entries/orchestrator.log/?raw",
        )
        assert response.status_code == 404

    def test_raw_rejects_non_get_methods(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.post(
            f"/api/pipelines/{pipeline_queued.id}/logs/entries/orchestrator.log/?raw",
        )
        assert response.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/pipelines/<uuid:id>/logs/all/
#  Returns entries from ALL log files merged and sorted by timestamp,
#  including per-pipeline files (stage logs, orchestrator.log) and the
#  shared system-level orchestrator.log at the LOG_ROOT root.
# ═══════════════════════════════════════════════════════════════════════


class TestLogsAll:
    """GET /api/pipelines/<id>/logs/all/ — all log entries merged for a pipeline."""

    @staticmethod
    def url(pipeline_id: str) -> str:
        return f"/api/pipelines/{pipeline_id}/logs/all/"

    # ------------------------------------------------------------------ #
    #  Tests – all
    # ------------------------------------------------------------------ #

    def test_merges_multiple_log_files_sorted_by_timestamp(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Entries from RED.log, orchestrator.log, and the shared system
        orchestrator.log are merged into a single sorted list."""
        create_pipeline_log(temp_log_root, pipeline_queued.id, "RED.log", [
            {"ts": "2026-01-01T00:00:02Z", "msg": "from RED"},
        ])
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log", [
            {"ts": "2026-01-01T00:00:01Z", "msg": "from orch"},
        ])
        create_log_file(temp_log_root, "orchestrator.log", [
            {"ts": "2026-01-01T00:00:03Z", "msg": "from system"},
        ])

        response = client.get(self.url(pipeline_queued.id))
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) == 3
        # Assert sorted by ts ascending
        assert entries[0]["msg"] == "from orch"
        assert entries[1]["msg"] == "from RED"
        assert entries[2]["msg"] == "from system"

    def test_respects_lines_parameter(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """``?lines=N`` returns only the last N entries across all merged files."""
        entries = [{"ts": f"2026-01-01T00:00:{i:02d}Z", "msg": f"msg{i}"} for i in range(10)]
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log", entries)

        response = client.get(f"{self.url(pipeline_queued.id)}?lines=3")
        result = response.json()["entries"]
        assert len(result) == 3
        assert result[0]["msg"] == "msg7"
        assert result[2]["msg"] == "msg9"

    def test_empty_when_no_log_files(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.get(self.url(pipeline_queued.id))
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_skips_invalid_json_lines_in_individual_files(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Invalid JSON lines in a file are skipped during merge."""
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "RED.log").write_text(
            '{"ts":"t1","msg":"valid"}\n'
            "not valid json\n"
            '{"ts":"t2","msg":"also valid"}\n'
        )
        response = client.get(self.url(pipeline_queued.id))
        entries = response.json()["entries"]
        assert len(entries) == 2
        assert entries[0]["msg"] == "valid"

    def test_handles_json_array_files(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """Pretty-printed JSON array files (stage logs) are parsed correctly."""
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "GREEN.log").write_text(
            json.dumps([
                {"ts": "2026-01-01T00:00:00Z", "msg": "first"},
                {"ts": "2026-01-01T00:00:01Z", "msg": "second"},
            ], indent=2)
        )
        response = client.get(self.url(pipeline_queued.id))
        entries = response.json()["entries"]
        assert len(entries) == 2
        assert entries[0]["msg"] == "first"
        assert entries[1]["msg"] == "second"

    def test_404_for_nonexistent_pipeline(
        self, client, db, temp_log_root,
    ):
        response = client.get(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/logs/all/",
        )
        assert response.status_code == 404

    def test_rejects_non_get_methods(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.post(self.url(pipeline_queued.id))
        assert response.status_code == 405

    def test_put_rejected(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.put(self.url(pipeline_queued.id))
        assert response.status_code == 405

    def test_delete_rejected(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        response = client.delete(self.url(pipeline_queued.id))
        assert response.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/logs/system/
#  Returns entries from the shared system-level orchestrator.log at
#  LOG_ROOT, written by the ``orchestrator`` logger (startup, agent
#  network, orphan reaping, etc.).
# ═══════════════════════════════════════════════════════════════════════


class TestSystemLogs:
    """GET /api/logs/system/ — system-level orchestrator log entries."""

    @staticmethod
    def url() -> str:
        return "/api/logs/system/"

    # ------------------------------------------------------------------ #
    #  Tests – system
    # ------------------------------------------------------------------ #

    def test_returns_system_log_entries(
        self, client, db, temp_log_root,
    ):
        """Returns entries from {LOG_ROOT}/orchestrator.log."""
        create_log_file(temp_log_root, "orchestrator.log", [
            {"ts": "2026-01-01T00:00:00Z", "msg": "Orchestrator started"},
        ])
        response = client.get(self.url())
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["msg"] == "Orchestrator started"

    def test_respects_lines_parameter(
        self, client, db, temp_log_root,
    ):
        entries = [{"msg": f"line{i}"} for i in range(10)]
        create_log_file(temp_log_root, "orchestrator.log", entries)

        response = client.get(f"{self.url()}?lines=3")
        result = response.json()["entries"]
        assert len(result) == 3
        assert result[0]["msg"] == "line7"
        assert result[2]["msg"] == "line9"

    def test_empty_when_no_system_log(
        self, client, db, temp_log_root,
    ):
        """Return empty array when {LOG_ROOT}/orchestrator.log does not exist."""
        response = client.get(self.url())
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_rejects_non_get_methods(
        self, client, db, temp_log_root,
    ):
        response = client.post(self.url())
        assert response.status_code == 405

    def test_put_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.put(self.url())
        assert response.status_code == 405

    def test_delete_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.delete(self.url())
        assert response.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/logs/django/
#  Returns entries from the Django application log (django.log) at
#  LOG_ROOT, written by the root logger's RotatingFileHandler
#  (Django-level errors, tracebacks).
# ═══════════════════════════════════════════════════════════════════════


class TestDjangoLogs:
    """GET /api/logs/django/ — Django application log entries."""

    @staticmethod
    def url() -> str:
        return "/api/logs/django/"

    # ------------------------------------------------------------------ #
    #  Tests – django
    # ------------------------------------------------------------------ #

    def test_returns_django_log_entries(
        self, client, db, temp_log_root,
    ):
        """Returns entries from {LOG_ROOT}/django.log."""
        create_log_file(temp_log_root, "django.log", [
            {"ts": "2026-01-01T00:00:00Z", "msg": "Database error"},
        ])
        response = client.get(self.url())
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["msg"] == "Database error"

    def test_respects_lines_parameter(
        self, client, db, temp_log_root,
    ):
        entries = [{"msg": f"line{i}"} for i in range(10)]
        create_log_file(temp_log_root, "django.log", entries)

        response = client.get(f"{self.url()}?lines=3")
        result = response.json()["entries"]
        assert len(result) == 3
        assert result[0]["msg"] == "line7"
        assert result[2]["msg"] == "line9"

    def test_empty_when_no_django_log(
        self, client, db, temp_log_root,
    ):
        """Return empty array when {LOG_ROOT}/django.log does not exist."""
        response = client.get(self.url())
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_rejects_non_get_methods(
        self, client, db, temp_log_root,
    ):
        response = client.post(self.url())
        assert response.status_code == 405

    def test_put_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.put(self.url())
        assert response.status_code == 405

    def test_delete_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.delete(self.url())
        assert response.status_code == 405


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/logs/spa/
#  Consolidated endpoint for the SPA frontend.  Always returns system
#  orchestrator.log and django.log entries.  When ?pipeline_id=UUID is
#  provided, also returns per-pipeline log files and merged entries.
#
#  Supports ?lines=N (default 100).
# ═══════════════════════════════════════════════════════════════════════


class TestLogsSpa:
    """GET /api/logs/spa/ — consolidated log data for the SPA."""

    @staticmethod
    def url() -> str:
        return "/api/logs/spa/"

    # ------------------------------------------------------------------ #
    #  Tests – spa
    # ------------------------------------------------------------------ #

    def test_returns_system_and_django_when_no_pipeline_id(
        self, client, db, temp_log_root,
    ):
        """Without pipeline_id, returns only system and django log entries."""
        create_log_file(temp_log_root, "orchestrator.log", [
            {"ts": "t1", "msg": "system event"},
        ])
        create_log_file(temp_log_root, "django.log", [
            {"ts": "t2", "msg": "django error"},
        ])

        response = client.get(self.url())
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "django" in data
        assert "pipeline" not in data
        assert len(data["system"]) == 1
        assert data["system"][0]["msg"] == "system event"
        assert len(data["django"]) == 1
        assert data["django"][0]["msg"] == "django error"

    def test_returns_pipeline_data_when_pipeline_id_provided(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        """With ?pipeline_id=, also returns pipeline files and merged entries."""
        create_log_file(temp_log_root, "orchestrator.log", [
            {"ts": "t1", "msg": "system event"},
        ])
        create_log_file(temp_log_root, "django.log", [
            {"ts": "t2", "msg": "django error"},
        ])
        create_pipeline_log(temp_log_root, pipeline_queued.id, "orchestrator.log", [
            {"ts": "t3", "msg": "pipeline event"},
        ])
        create_pipeline_log(temp_log_root, pipeline_queued.id, "RED.log", [
            {"ts": "t4", "msg": "stage event"},
        ])

        response = client.get(f"{self.url()}?pipeline_id={pipeline_queued.id}")
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "django" in data
        assert "pipeline" in data
        assert "files" in data["pipeline"]
        assert "entries" in data["pipeline"]
        assert sorted(data["pipeline"]["files"]) == ["RED.log", "orchestrator.log"]
        assert len(data["pipeline"]["entries"]) == 2

    def test_respects_lines_parameter(
        self, client, db, temp_log_root,
    ):
        entries = [{"msg": f"line{i}"} for i in range(10)]
        create_log_file(temp_log_root, "orchestrator.log", entries)
        create_log_file(temp_log_root, "django.log", entries)

        response = client.get(f"{self.url()}?lines=3")
        data = response.json()
        assert len(data["system"]) == 3
        assert len(data["django"]) == 3

    def test_404_for_nonexistent_pipeline_id(
        self, client, db, temp_log_root,
    ):
        """An invalid (non-existent) pipeline_id returns 404."""
        create_log_file(temp_log_root, "orchestrator.log", [
            {"ts": "t1", "msg": "system"},
        ])
        response = client.get(
            f"{self.url()}?pipeline_id=00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    def test_empty_when_no_log_files(
        self, client, db, temp_log_root,
    ):
        """Return empty arrays when no log files exist."""
        response = client.get(self.url())
        assert response.status_code == 200
        data = response.json()
        assert data["system"] == []
        assert data["django"] == []

    def test_rejects_non_get_methods(
        self, client, db, temp_log_root,
    ):
        response = client.post(self.url())
        assert response.status_code == 405

    def test_put_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.put(self.url())
        assert response.status_code == 405

    def test_delete_rejected(
        self, client, db, temp_log_root,
    ):
        response = client.delete(self.url())
        assert response.status_code == 405
