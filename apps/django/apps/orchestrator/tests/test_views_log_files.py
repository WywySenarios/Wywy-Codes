"""Tests for log file listing and per-file log entry endpoints.

These tests cover:
  - GET  /api/pipelines/<uuid:id>/logs/              — list available log files
  - GET  /api/pipelines/<uuid:id>/logs/entries/<str:filename>/   — JSON entries, single file
  - GET  /api/pipelines/<uuid:id>/logs/entries/<str:filename>/?raw — raw text dump
"""

from __future__ import annotations

import json
from pathlib import Path


class TestLogFilesList:
    """GET /api/pipelines/<id>/logs/ — list available log filenames."""

    @staticmethod
    def url(pipeline) -> str:
        return f"/api/pipelines/{pipeline.id}/logs/"

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def create_log_file(
        log_root: Path, pipeline_id: str, filename: str,
        entries: list[dict] | None = None,
    ) -> Path:
        log_dir = log_root / str(pipeline_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / filename
        if entries is not None:
            lines = "\n".join(json.dumps(e) for e in entries) + "\n"
            log_file.write_text(lines)
        else:
            log_file.touch()
        return log_file

    # ------------------------------------------------------------------ #
    #  Tests – list
    # ------------------------------------------------------------------ #

    def test_list_returns_available_filenames(
        self, client, db, pipeline_queued, temp_log_root,
    ):
        self.create_log_file(temp_log_root, pipeline_queued.id, "orchestrator.log")
        self.create_log_file(temp_log_root, pipeline_queued.id, "RED.log")

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
        self.create_log_file(temp_log_root, pipeline_queued.id, "orchestrator.log")
        self.create_log_file(temp_log_root, pipeline_queued.id, ".hidden.log")

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

    @staticmethod
    def create_log_file(
        log_root: Path, pipeline_id: str, filename: str,
        entries: list[dict],
    ) -> Path:
        log_dir = log_root / str(pipeline_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / filename
        lines = "\n".join(json.dumps(e) for e in entries) + "\n"
        log_file.write_text(lines)
        return log_file

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
        self.create_log_file(temp_log_root, pipeline_queued.id, "orchestrator.log", entries)

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
        self.create_log_file(temp_log_root, pipeline_queued.id, "RED.log", [{"msg": "from stage"}])
        self.create_log_file(
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
        self.create_log_file(temp_log_root, pipeline_queued.id, "orchestrator.log", entries)

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
