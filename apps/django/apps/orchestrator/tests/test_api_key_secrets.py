"""Test that API keys are read from secret files (Docker secrets),
not from Django settings (environment variables / .env files).

**The bug:** All four API keys (OPENCODE, DEEPSEEK, OPENAI, ANTHROPIC) are
currently passed to the opencode server container via::

    environment["OPENCODE_API_KEY"] = getattr(settings, "AGENT_OPENCODE_API_KEY", "")

The corresponding settings come from ``.env`` variables, which are unset in
production; every API key ends up as an empty string.  The opencode server
starts but hangs when asked to create a session because no LLM provider can
be initialised.

**Contract:** Each API key must be read from a secret file (e.g. a Docker
secret mounted at ``/run/secrets/``) so that the value is available to the
container even when the ``.env`` file has no key.  This follows the same
pattern already established by ``_read_github_token`` in ``orchestrator.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline


# ── Mock Docker helpers (mirror test_agent_docker_params.py) ────────────────


class MockContainer:
    short_id = "abc12345"

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        return {"StatusCode": 0}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return b""

    def remove(self, force: bool = True) -> None:
        pass

    def reload(self) -> None:
        pass


class MockContainers:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> MockContainer:
        self.run_calls.append(kwargs)
        return MockContainer()


# ── Tests ───────────────────────────────────────────────────────────────────


class TestApiKeyReadHelper:
    """``orchestrator._read_api_key`` must exist and return the content of a
    secret file.  The test will fail (RED) with an AttributeError because the
    function does not exist yet — it will be created in the GREEN phase."""

    def test_read_api_key_returns_content_of_secret_file(
        self, tmp_path: Path,
    ) -> None:
        """``orchestrator._read_api_key(key_name, secrets_dir)`` must return
        the contents of ``{secrets_dir}/{normalised-key-name}`` when the file
        exists.

        The normalised key name is the ``key_name`` lowercased with underscores
        replaced by hyphens (e.g. ``"OPENCODE_API_KEY"`` →
        ``"opencode-api-key"``).

        This function does not exist yet — the test will raise
        ``AttributeError`` (RED).
        """
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "opencode-api-key"
        secret_file.write_text("sk-from-secret")

        # This call WILL FAIL because orchestrator._read_api_key does not
        # exist yet.  That is the intended RED result.
        result = orchestrator._read_api_key(
            "OPENCODE_API_KEY", str(secrets_dir),
        )

        assert result == "sk-from-secret", (
            f"Expected 'sk-from-secret', got {result!r}"
        )

    def test_read_api_key_returns_empty_when_file_missing(
        self, tmp_path: Path,
    ) -> None:
        """When the secret file does not exist ``_read_api_key`` must return
        an empty string."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()

        result = orchestrator._read_api_key(
            "OPENCODE_API_KEY", str(secrets_dir),
        )

        assert result == "", (
            f"Expected empty string for missing secret file, got {result!r}"
        )


class TestApiKeysInContainerEnvironment:
    """The ``_start_opencode_server`` function must inject API keys into the
    container environment by reading from secret files rather than from Django
    settings.

    Note: This class relies on ``orchestrator._read_api_key`` which does not
    exist yet.  The tests will fail (RED) until that function is created.
    """

    def _start_server_and_get_env(
        self,
        db: None,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> dict[str, str]:
        """Helper — create a pipeline, mock Docker, start the server and return
        the environment dict passed to ``containers.run()``."""
        pipeline = Pipeline.objects.create(
            invocation_name="key-env-test",
            description="Test API keys in container env",
            status="queued",
        )
        orchestrator._create_workspace(pipeline)

        mock_containers = MockContainers()
        mock_client = type("MockClient", (), {"containers": mock_containers})()
        monkeypatch.setattr(orchestrator.docker, "from_env", lambda: mock_client)

        orchestrator._start_opencode_server(pipeline)

        assert len(mock_containers.run_calls) == 1
        return mock_containers.run_calls[0].get("environment", {})

    def test_opencode_api_key_is_non_empty_when_secret_exists(
        self,
        db: None,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The OPENCODE_API_KEY in the container environment MUST be
        non-empty when a secret file exists.

        Currently the key is empty because ``_start_opencode_server`` reads
        from ``getattr(settings, "AGENT_OPENCODE_API_KEY", "")`` which is
        empty.  After GREEN, ``_start_opencode_server`` will call
        ``_read_api_key`` instead.
        """
        # Set up a secret file and monkeypatch the reading function so that
        # _start_opencode_server picks it up.  The function doesn't exist yet
        # → this test will fail (RED).
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret_file = secrets_dir / "opencode-api-key"
        secret_file.write_text("sk-real-key-from-secret")

        # Provide the secrets dir to the code; this setting does not exist yet
        # either, but override_settings handles arbitrary keys.
        from django.test import override_settings

        with override_settings(API_KEY_SECRETS_DIR=str(secrets_dir)):
            env = self._start_server_and_get_env(
                db, patched_copy_sources, temp_workspace, temp_log_root,
                monkeypatch,
            )

            actual = env.get("OPENCODE_API_KEY", "")
            assert actual, (
                f"OPENCODE_API_KEY must be non-empty when the secret file "
                f"exists, but got {actual!r}.  "
                f"Current code reads from settings.AGENT_OPENCODE_API_KEY "
                f"(={getattr(settings, 'AGENT_OPENCODE_API_KEY', '<MISSING>')!r}) "
                f"which is empty.  The feature to read from secret files has "
                f"not been implemented yet."
            )

    def test_all_four_api_keys_are_non_empty_when_secrets_exist(
        self,
        db: None,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """All four API keys (OPENCODE, DEEPSEEK, OPENAI, ANTHROPIC) must
        be non-empty when their respective secret files exist."""
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()

        env_var_files = {
            "OPENCODE_API_KEY": "opencode-api-key",
            "DEEPSEEK_API_KEY": "deepseek-api-key",
            "OPENAI_API_KEY": "openai-api-key",
            "ANTHROPIC_API_KEY": "anthropic-api-key",
        }
        for env_var, filename in env_var_files.items():
            (secrets_dir / filename).write_text(f"sk-{env_var.lower()}")

        from django.test import override_settings

        with override_settings(API_KEY_SECRETS_DIR=str(secrets_dir)):
            env = self._start_server_and_get_env(
                db, patched_copy_sources, temp_workspace, temp_log_root,
                monkeypatch,
            )

            for env_var in env_var_files:
                actual = env.get(env_var, "")
                assert actual, (
                    f"{env_var} must be non-empty when the secret file "
                    f"exists, but got {actual!r}"
                )

    def test_api_key_is_empty_when_secret_file_missing(
        self,
        db: None,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When the secret file does not exist the API key MUST default to
        an empty string so the container starts without attempting to
        authenticate with a missing credential."""
        from django.test import override_settings

        with override_settings(API_KEY_SECRETS_DIR="/tmp/nonexistent-secrets"):
            env = self._start_server_and_get_env(
                db, patched_copy_sources, temp_workspace, temp_log_root,
                monkeypatch,
            )

            assert env.get("OPENCODE_API_KEY", "") == "", (
                f"Expected empty API key when secret file is missing, "
                f"got '{env.get('OPENCODE_API_KEY')}'"
            )
