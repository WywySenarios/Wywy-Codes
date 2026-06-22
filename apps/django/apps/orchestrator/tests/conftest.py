"""Shared fixtures for orchestrator tests."""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings
from django.test import override_settings

from apps.orchestrator.models import Pipeline, PipelineStage


@pytest.fixture
def pipeline_queued(db) -> Pipeline:
    return Pipeline.objects.create(
        invocation_name="test-pipeline",
        description="A test pipeline",
        status="queued",
    )


@pytest.fixture
def pipeline_running(db) -> Pipeline:
    pipeline = Pipeline.objects.create(
        invocation_name="running-pipeline",
        description="Pipeline in progress",
        status="running",
        current_stage="RED",
        iteration_count=1,
    )
    stage_names = [
        "init",
        "RED",
        "GREEN",
        "REFRACTOR",
        "compilance",
        "PR writer",
    ]
    for name in stage_names:
        PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
    PipelineStage.objects.filter(pipeline=pipeline, name="init").update(status="completed")
    PipelineStage.objects.filter(pipeline=pipeline, name="RED").update(status="running")
    return pipeline


@pytest.fixture
def pipeline_awaiting_input(db) -> Pipeline:
    return Pipeline.objects.create(
        invocation_name="awaiting-input",
        description="Pipeline waiting for user",
        status="running",
        current_stage="GREEN",
        user_input_pending=True,
    )


@pytest.fixture
def pipeline_blocked_with_session(db) -> Pipeline:
    """A pipeline blocked on GREEN with a ``session_id`` on the blocked stage."""
    pipeline = Pipeline.objects.create(
        invocation_name="blocked-with-session",
        description="Pipeline blocked with session_id",
        status="running",
        current_stage="GREEN",
        user_input_pending=True,
    )
    stage_names = [
        "init",
        "RED",
        "GREEN",
        "REFRACTOR",
        "compilance",
        "PR writer",
    ]
    for name in stage_names:
        PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
    PipelineStage.objects.filter(pipeline=pipeline, name="init").update(status="completed")
    PipelineStage.objects.filter(pipeline=pipeline, name="RED").update(status="completed")
    PipelineStage.objects.filter(pipeline=pipeline, name="GREEN").update(
        status="blocked", session_id="sess_123"
    )
    return pipeline


@pytest.fixture
def pipeline_blocked_wo_session(db) -> Pipeline:
    """A pipeline blocked on GREEN with **no** ``session_id`` on the blocked stage."""
    pipeline = Pipeline.objects.create(
        invocation_name="blocked-wo-session",
        description="Pipeline blocked without session_id",
        status="running",
        current_stage="GREEN",
        user_input_pending=True,
    )
    stage_names = [
        "init",
        "RED",
        "GREEN",
        "REFRACTOR",
        "compilance",
        "PR writer",
    ]
    for name in stage_names:
        PipelineStage.objects.create(pipeline=pipeline, name=name, status="pending")
    PipelineStage.objects.filter(pipeline=pipeline, name="init").update(status="completed")
    PipelineStage.objects.filter(pipeline=pipeline, name="RED").update(status="completed")
    PipelineStage.objects.filter(pipeline=pipeline, name="GREEN").update(status="blocked")
    return pipeline


@pytest.fixture
def pipeline_completed(db) -> Pipeline:
    return Pipeline.objects.create(
        invocation_name="completed-pipeline",
        description="Done pipeline",
        status="completed",
        current_stage="PR writer",
        pr_url="https://github.com/test/pr/1",
    )


@pytest.fixture
def pipeline_failed(db) -> Pipeline:
    return Pipeline.objects.create(
        invocation_name="failed-pipeline",
        description="Failed pipeline",
        status="failed",
        current_stage="GREEN",
    )


@pytest.fixture
def pipeline_cancelled(db) -> Pipeline:
    return Pipeline.objects.create(
        invocation_name="cancelled-pipeline",
        description="Cancelled pipeline",
        status="cancelled",
    )


@pytest.fixture
def temp_workspace(monkeypatch: MonkeyPatch) -> Generator[Path]:
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator.MIN_DISK_SPACE_GB", 0,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(WORKSPACE_ROOT=tmpdir):
            yield Path(tmpdir)


@pytest.fixture
def temp_log_root() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(LOG_ROOT=tmpdir):
            yield Path(tmpdir)


@pytest.fixture
def source_trees() -> Generator[dict[str, str]]:
    """Create two temporary source trees mimicking /etc/Wywy-Website-Control
    and /usr/local/Wywy-Website, each with git repos.

    The trees are created under a single base directory so that
    os.path.relpath(source, base) produces the expected relative paths
    (e.g. 'etc/Wywy-Website-Control', 'usr/local/Wywy-Website').
    """
    with tempfile.TemporaryDirectory() as base_tmp:
        base = Path(base_tmp)
        control_dir = base / "etc" / "Wywy-Website-Control"
        control_dir.mkdir(parents=True)

        (control_dir / "README.md").write_text("# Control")
        subprocess.run(["git", "init"], cwd=str(control_dir), check=True)
        subprocess.run(
            ["git", "add", "."], cwd=str(control_dir), check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init", "--allow-empty"],
            cwd=str(control_dir), check=True,
            capture_output=True,
        )

        wywy_base = base / "usr" / "local" / "Wywy-Website"

        wywy_main = wywy_base / "Wywy-Website"
        wywy_main.mkdir(parents=True)
        (wywy_main / "README.md").write_text("# Wywy-Website")
        subprocess.run(["git", "init"], cwd=str(wywy_main), check=True)
        subprocess.run(["git", "add", "."], cwd=str(wywy_main), check=True)
        subprocess.run(
            ["git", "commit", "-m", "init", "--allow-empty"],
            cwd=str(wywy_main), check=True,
            capture_output=True,
        )

        wywy_cache = wywy_base / "Wywy-Website-Cache"
        wywy_cache.mkdir()
        (wywy_cache / "README.md").write_text("# Cache")
        subprocess.run(["git", "init"], cwd=str(wywy_cache), check=True)
        subprocess.run(["git", "add", "."], cwd=str(wywy_cache), check=True)
        subprocess.run(
            ["git", "commit", "-m", "init", "--allow-empty"],
            cwd=str(wywy_cache), check=True,
            capture_output=True,
        )

        for name in ("Wywy-Website-Master-Database", "Wywy-Website-Backup"):
            repo_path = wywy_base / name
            repo_path.mkdir()
            (repo_path / "README.md").write_text(f"# {name}")
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True)
            subprocess.run(
                ["git", "commit", "-m", "init", "--allow-empty"],
                cwd=str(repo_path), check=True,
                capture_output=True,
            )

        yield {
            "base": str(base),
            "control": str(control_dir),
            "wywy_website": str(wywy_base),
        }


@pytest.fixture
def patched_copy_sources(source_trees: dict[str, str], monkeypatch: MonkeyPatch) -> dict[str, str]:
    """Override COPY_SOURCES and _COPY_SOURCES_BASE to point at temp dirs."""
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator.COPY_SOURCES",
        [source_trees["control"], source_trees["wywy_website"]],
    )
    monkeypatch.setattr(
        "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
        source_trees["base"],
    )
    return source_trees

