"""Opencode JSON config writer — generates ``.opencode/opencode.json``
with per-stage model overrides, server settings, and provider exclusions.

The opencode server reads this file at startup to configure tools, models,
and permissions per stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.orchestrator.models import Pipeline
from apps.orchestrator.orchestrator import STAGE_ORDER


def _detect_disabled_providers() -> list[str]:
    """Return a list of provider names whose API keys are missing.

    Checks Django settings for known provider API key constants.
    A provider is disabled when its key is empty or missing.
    """
    provider_keys: dict[str, str] = {
        "openai": "AGENT_OPENAI_API_KEY",
        "anthropic": "AGENT_ANTHROPIC_API_KEY",
        "deepseek": "AGENT_DEEPSEEK_API_KEY",
        "opencode": "AGENT_OPENCODE_API_KEY",
    }
    disabled: list[str] = []
    for provider, key_name in provider_keys.items():
        value = getattr(settings, key_name, "")
        if not value:
            disabled.append(provider)
    return disabled


def _build_config(pipeline: Pipeline) -> dict[str, Any]:
    """Build the full opencode config dict for *pipeline*."""
    default_model = settings.OPENCODE_DEFAULT_MODEL
    small_model = settings.OPENCODE_SMALL_MODEL
    stage_map: dict[str, dict[str, str]] = getattr(
        settings, "STAGE_MODEL_MAP", {}
    )

    agents: dict[str, dict[str, str]] = {}
    for stage_name in STAGE_ORDER:
        overrides = stage_map.get(stage_name, {})
        agents[stage_name] = {
            "model": overrides.get("model", default_model),
        }

    return {
        "model": default_model,
        "small_model": small_model,
        "permission": "allow",
        "server": {
            "port": settings.OPENCODE_SERVER_PORT,
            "hostname": settings.OPENCODE_SERVER_HOSTNAME,
        },
        "disabled_providers": _detect_disabled_providers(),
        "agent": agents,
        "snapshot": True,
        "compaction": {"auto": True},
    }


def write_pipeline_config(workspace: Path, pipeline: Pipeline) -> None:
    """Write ``.opencode/opencode.json`` in *workspace* for *pipeline*.

    Creates the ``.opencode/`` subdirectory if it does not exist.
    """
    config_dir = workspace / ".opencode"
    config_dir.mkdir(parents=True, exist_ok=True)

    config = _build_config(pipeline)
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))
