"""Tests for GET /api/pipelines/<uuid:id>/logs/<str:stage_name>/ endpoint."""

from __future__ import annotations

import json
from pathlib import Path


class TestLogTail:
    @staticmethod
    def url(pipeline, stage_name="planner") -> str:
        return f"/api/pipelines/{pipeline.id}/logs/{stage_name}/"

    @staticmethod
    def create_log_file(log_root: Path, pipeline_id: str, stage_name: str, entries: list[dict]):
        log_dir = log_root / str(pipeline_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{stage_name}.log"
        lines = "\n".join(json.dumps(entry) for entry in entries) + "\n"
        log_file.write_text(lines)

    def test_404_for_nonexistent_pipeline(self, client, db, temp_log_root):
        response = client.get(
            "/api/pipelines/00000000-0000-0000-0000-000000000000/logs/planner/"
        )
        assert response.status_code == 404

    def test_rejects_non_get_methods(self, client, db, pipeline_queued, temp_log_root):
        response = client.post(self.url(pipeline_queued))
        assert response.status_code == 405

    def test_empty_logs_when_no_files(self, client, db, pipeline_queued, temp_log_root):
        response = client.get(self.url(pipeline_queued))
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_returns_log_entries(self, client, db, pipeline_queued, temp_log_root):
        entries = [
            {"ts": "2026-01-01T00:00:00.000Z", "level": "INFO", "msg": "start"},
            {"ts": "2026-01-01T00:00:01.000Z", "level": "ERROR", "msg": "fail"},
        ]
        self.create_log_file(temp_log_root, pipeline_queued.id, "planner", entries)

        response = client.get(self.url(pipeline_queued, "planner"))
        assert response.status_code == 200
        result = response.json()["entries"]
        assert len(result) == 2
        assert result[0]["msg"] == "start"
        assert result[1]["msg"] == "fail"

    def test_includes_orchestrator_log(self, client, db, pipeline_queued, temp_log_root):
        stage_entries = [
            {"ts": "2026-01-01T00:00:00.000Z", "level": "INFO", "msg": "stage entry"},
        ]
        orch_entries = [
            {"ts": "2026-01-01T00:00:02.000Z", "level": "INFO", "msg": "orch entry"},
        ]
        self.create_log_file(temp_log_root, pipeline_queued.id, "planner", stage_entries)
        self.create_log_file(temp_log_root, pipeline_queued.id, "orchestrator", orch_entries)

        response = client.get(self.url(pipeline_queued, "planner"))
        result = response.json()["entries"]
        assert len(result) == 2
        msgs = {e["msg"] for e in result}
        assert msgs == {"stage entry", "orch entry"}

    def test_skips_invalid_json_lines(self, client, db, pipeline_queued, temp_log_root):
        log_dir = temp_log_root / str(pipeline_queued.id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "planner.log"
        log_file.write_text(
            '{"ts":"t","level":"INFO","msg":"valid"}\n'
            "not valid json\n"
            '{"ts":"t","level":"ERROR","msg":"also valid"}\n'
        )

        response = client.get(self.url(pipeline_queued, "planner"))
        result = response.json()["entries"]
        assert len(result) == 2
        assert result[0]["msg"] == "valid"
        assert result[1]["msg"] == "also valid"

    def test_respects_lines_parameter(self, client, db, pipeline_queued, temp_log_root):
        entries = [{"msg": f"line{i}"} for i in range(10)]
        self.create_log_file(temp_log_root, pipeline_queued.id, "planner", entries)

        response = client.get(f"{self.url(pipeline_queued, 'planner')}?lines=3")
        result = response.json()["entries"]
        assert len(result) == 3
        assert result[0]["msg"] == "line7"
        assert result[2]["msg"] == "line9"
