"""Tests for the opencode JSON config writer — generates ``opencode.json``
with per-stage model overrides, server settings, and provider exclusions.

The config writer is responsible for producing the ``.opencode/opencode.json``
file that the opencode server reads at startup to configure tools, models,
and permissions per stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.test import override_settings

from apps.orchestrator.models import Pipeline

EXPECTED_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"
EXPECTED_SMALL_MODEL = "anthropic/claude-haiku-4-5"
EXPECTED_STAGE_ORDER = [
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compliance",
    "PR writer",
]


# ── helpers ────────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline_with_init_complete(db) -> Generator[Pipeline]:
    """A running pipeline with init stage completed, ready for config writing."""
    pipeline = Pipeline.objects.create(
        invocation_name="config-writer-test",
        description="Test opencode config generation",
        status="running",
    )
    yield pipeline


# ── RED: config_writer module must exist ───────────────────────────────────


def test_config_writer_module_exists() -> None:
    """The ``config_writer`` module must exist under
    ``apps.orchestrator.config_writer``."""
    # This import FAILS — the module doesn't exist yet.
    import apps.orchestrator.config_writer  # noqa: F401


def test_write_pipeline_config_function_exists() -> None:
    """The module must expose ``write_pipeline_config``."""
    from apps.orchestrator.config_writer import write_pipeline_config  # noqa: F401


# ── RED: config file structure ─────────────────────────────────────────────


def test_write_pipeline_config_creates_opencode_json(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """``write_pipeline_config`` must create ``.opencode/opencode.json``
    in the given workspace directory."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)

    config_path = tmp_path / ".opencode" / "opencode.json"
    assert config_path.exists(), (
        f"Expected {config_path} to be created by write_pipeline_config"
    )


def test_config_has_default_model(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config must include a ``model`` key with the default model."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    assert "model" in config, "Config must have a 'model' key"
    assert isinstance(config["model"], str), "'model' must be a string"


def test_config_has_small_model(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config must include a ``small_model`` key for cheaper tasks."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    assert "small_model" in config, "Config must have a 'small_model' key"
    assert isinstance(config["small_model"], str), "'small_model' must be a string"


def test_config_permission_is_allow(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config must set ``permission`` to ``"allow"`` so the agent runs
    autonomously without prompting for tool approval."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    assert config.get("permission") == "allow", (
        f"Expected permission='allow', got '{config.get('permission')}'"
    )


def test_config_has_server_settings(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config must include ``server`` dict with ``port`` and
    ``hostname`` matching the opencode server container settings."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    server = config.get("server", {})
    assert server.get("port") == 4096, (
        f"Expected server.port=4096, got {server.get('port')}"
    )
    assert server.get("hostname") == "0.0.0.0", (
        f"Expected server.hostname='0.0.0.0', got {server.get('hostname')}"
    )


def test_config_has_per_stage_agents(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config must include an ``agent`` dict with one entry per stage,
    each containing at minimum a ``model`` key."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    agent = config.get("agent", {})
    assert isinstance(agent, dict), "'agent' must be a dict"

    for stage in EXPECTED_STAGE_ORDER:
        assert stage in agent, (
            f"Agent config missing stage '{stage}'. "
            f"Got stages: {list(agent.keys())}"
        )
        assert "model" in agent[stage], (
            f"Stage '{stage}' agent config must have a 'model' key"
        )
        assert isinstance(agent[stage]["model"], str), (
            f"Stage '{stage}' model must be a string"
        )


# ── RED: snapshot / compaction ─────────────────────────────────────────────


def test_config_has_snapshot_enabled(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config should enable snapshots for session recovery."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    assert config.get("snapshot") is True, (
        "Expected snapshot=True for session recovery support. "
        f"Got: {config.get('snapshot')}"
    )


def test_config_has_auto_compaction(
    pipeline_with_init_complete: Pipeline,
    tmp_path: Path,
) -> None:
    """The config should enable auto compaction for long-running sessions."""
    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(tmp_path, pipeline_with_init_complete)
    config = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())

    compaction = config.get("compaction", {})
    assert compaction.get("auto") is True, (
        "Expected compaction.auto=True. "
        f"Got: {compaction.get('auto')}"
    )
