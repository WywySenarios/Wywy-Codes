"""Tests for workspace creation and teardown (copytree-based)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline


class TestWorkspaceDirStructure:
    def test_workspace_dir_structure(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """All subdirs created: copies/, state/, artifacts/, context/, context/user-input/, .opencode/"""
        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        assert workspace.exists()

        expected_dirs = [
            "copies",
            "state",
            "artifacts",
            "context",
            "context/user-input",
            ".opencode",
        ]
        for relative in expected_dirs:
            assert (workspace / relative).exists(), f"Missing: {relative}"

        assert (workspace / "copies" / "etc" / "Wywy-Website-Control").exists()
        assert (workspace / "copies" / "usr" / "local" / "Wywy-Website").exists()


class TestCopytreeGit:
    def test_copytree_preserves_git(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """.git/ directory intact in each repo copy; git log returns commits."""
        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        copies = workspace / "copies"

        control_repo = copies / "etc" / "Wywy-Website-Control"
        assert (control_repo / ".git").is_dir()
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(control_repo), capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""

        wywy_main = copies / "usr" / "local" / "Wywy-Website" / "Wywy-Website"
        assert (wywy_main / ".git").is_dir()
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(wywy_main), capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() != ""


class TestCopytreeContent:
    def test_copytree_content_match(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """File count, content, and structure match source for all repos (excluding .git internals)."""
        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        copies = workspace / "copies"

        expected_repos = [
            ("etc/Wywy-Website-Control", patched_copy_sources["control"]),
            ("usr/local/Wywy-Website", patched_copy_sources["wywy_website"]),
        ]
        for rel_path, source_root in expected_repos:
            dest = copies / rel_path
            assert dest.exists(), f"Missing: {rel_path}"

            src_files = sorted(
                p.relative_to(Path(source_root)).as_posix()
                for p in Path(source_root).rglob("*")
                if p.is_file() and ".git" not in p.parts
            )
            dest_files = sorted(
                p.relative_to(dest).as_posix()
                for p in dest.rglob("*")
                if p.is_file() and ".git" not in p.parts
            )
            for f in src_files:
                assert f in dest_files, f"Missing file in copy: {f}"
            for f in src_files:
                src_content = (Path(source_root) / f).read_text()
                dest_content = (dest / f).read_text()
                assert src_content == dest_content, f"Content mismatch for {f}"


class TestBranchCreation:
    def test_branch_created_all_repos(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """git checkout -b {invocation_name} succeeds in every repo copy; branch exists."""
        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        copies = workspace / "copies"

        from apps.orchestrator.orchestrator import REPO_CONFIG

        for repo in REPO_CONFIG:
            repo_path = copies / repo["mount"].lstrip("/")
            if repo_path.exists():
                result = subprocess.run(
                    ["git", "branch"],
                    cwd=str(repo_path), capture_output=True, text=True,
                )
                assert pipeline_queued.invocation_name in result.stdout, \
                    f"Branch not found in {repo['name']}"


class TestNoChown:
    def test_no_chown_called(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Monkeypatch os.chown to raise if called - test passes without chown."""
        def _fail_chown(*args, **kwargs):
            raise AssertionError("os.chown should not be called")

        monkeypatch.setattr(os, "chown", _fail_chown)
        orchestrator._create_workspace(pipeline_queued)

    def test_chown_not_called_on_workspace_dirs(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """No chown on copies_dir, state_dir, artifacts_dir, context_dir, log_dir."""
        calls = []

        def _record_chown(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(os, "chown", _record_chown)
        orchestrator._create_workspace(pipeline_queued)
        assert len(calls) == 0, f"os.chown called {len(calls)} times"


class TestVolumeMountPaths:
    @mock.patch("apps.orchestrator.orchestrator.docker")
    def test_volume_mount_paths(
        self,
        mock_docker: mock.MagicMock,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """Volumes dict uses copies/{mount_lstrip} not repos/{name}."""
        from apps.orchestrator.orchestrator import REPO_CONFIG

        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)

        from apps.orchestrator.models import PipelineStage
        stage = PipelineStage(pipeline=pipeline_queued, name="RED")

        # Reconstruct the volumes dict as _spawn_agent_container would
        volumes = {}
        for repo in REPO_CONFIG:
            repo_path = workspace / "copies" / repo["mount"].lstrip("/")
            if repo_path.exists():
                volumes[str(repo_path)] = {"bind": repo["mount"], "mode": "rw"}

        for repo in REPO_CONFIG:
            expected_bind = repo["mount"]
            found = False
            for vol_key, vol_cfg in volumes.items():
                if vol_cfg["bind"] == expected_bind:
                    found = True
                    assert repo["mount"].lstrip("/") in vol_key, \
                        f"Volume key should contain mount path: {vol_key}"
                    assert "copies" in vol_key, \
                        f"Volume key should contain 'copies': {vol_key}"
                    break
            assert found, f"No volume mount found for {repo['mount']}"


class TestStateJson:
    def test_state_json_paths(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """state.json records correct copies/ paths for each repo."""
        orchestrator._create_workspace(pipeline_queued)

        state_path = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id) / "state" / "state.json"
        assert state_path.exists()

        state = json.loads(state_path.read_text())
        repos = state["workspace"]["repos"]

        workspace_root = f"{settings.WORKSPACE_ROOT}/{pipeline_queued.id}"

        from apps.orchestrator.orchestrator import REPO_CONFIG
        for repo in REPO_CONFIG:
            expected_path = f"{workspace_root}/copies/{repo['mount'].lstrip('/')}"
            assert repos[repo["name"]] == expected_path, \
                f"Expected {expected_path}, got {repos[repo['name']]}"
            assert "copies" in repos[repo["name"]], \
                f"Path should contain 'copies': {repos[repo['name']]}"

    def test_state_json_no_chown(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """state.json written without chown call."""
        def _fail_chown(*args, **kwargs):
            raise AssertionError("os.chown should not be called")

        monkeypatch.setattr(os, "chown", _fail_chown)
        orchestrator._create_workspace(pipeline_queued)

        state_path = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id) / "state" / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["status"] == "running"


class TestPartialSources:
    def test_source_missing_partial(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Unreadable or missing source raises OSError (propagates to _execute_pipeline)."""
        readable = tmp_path / "source"
        readable.mkdir()
        (readable / "file.txt").write_text("hello")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(readable), "/nonexistent/path/xyz"],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        with pytest.raises(OSError):
            orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        assert workspace.exists()
        assert (workspace / "copies").exists()

    def test_source_missing_both(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Both sources missing raises OSError (propagates to _execute_pipeline)."""
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            ["/nonexistent/a", "/nonexistent/b"],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            "/",
        )

        with pytest.raises(OSError):
            orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        assert workspace.exists()
        assert (workspace / "copies").exists()


class TestWorkspaceTeardownOnCopyFailure:
    def test_workspace_torn_down_on_copy_failure(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When copytree fails, _execute_pipeline must tear down the half-created workspace.

        Regression for: removing the inner try/except around shutil.copytree
        lets OSError propagate to _execute_pipeline, which
        marks the pipeline 'failed' but never calls _teardown_workspace.
        The workspace directories and .opencode/opencode.json are orphaned.
        """
        import shutil as shutil_mod
        from apps.orchestrator.orchestrator import _execute_pipeline

        source1 = tmp_path / "source"
        source1.mkdir()
        (source1 / "file.txt").write_text("hello")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(source1)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        def _failing_copytree(src, dst, **kwargs):
            raise PermissionError("Permission denied")

        monkeypatch.setattr(shutil_mod, "copytree", _failing_copytree)

        # _execute_pipeline expects the pipeline to already be marked
        # "running" — the orchestrator loop does this before calling it.
        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        _execute_pipeline(pipeline_queued)

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed"

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        assert not workspace.exists(), (
            f"Workspace {workspace} should be torn down after copy failure, "
            f"but it still exists"
        )


class TestDiskSpace:
    def test_disk_space_check(self, monkeypatch: MonkeyPatch) -> None:
        """< 10GB free raises OSError."""
        # os.statvfs_result fields: f_bsize, f_frsize, f_blocks, f_bfree,
        #    f_bavail, f_files, f_ffree, f_favail, f_flag, f_namemax
        def _mock_statvfs(path):
            result = os.statvfs_result((4096, 4096, 0, 0, 0, 0, 0, 0, 0, 255))
            return result

        monkeypatch.setattr(os, "statvfs", _mock_statvfs)
        with pytest.raises(OSError, match="Insufficient disk space"):
            orchestrator._check_disk_space("/fake/path")


class TestTeardown:
    def test_teardown_removes_all(
        self,
        pipeline_completed: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """After _teardown_workspace(), workspace dir is gone."""
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_completed.id)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "state").mkdir(exist_ok=True)
        (workspace / "state" / "state.json").write_text("{}")

        assert workspace.exists()
        orchestrator._teardown_workspace(pipeline_completed)
        assert not workspace.exists()

    def test_teardown_idempotent_logs_once(
        self,
        pipeline_completed: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """_teardown_workspace must log "Tearing down workspace" only once.

        Once teardown has completed for a pipeline, subsequent calls must
        exit immediately without logging or any further side effects.

        Regression for: Tearing down workspace logged multiple times when
        _teardown_workspace is called again after the workspace dir has
        been recreated (or the first call partially failed).
        """
        log_entries: list[str] = []

        def _track_log(pipeline, level, msg, **kwargs):
            log_entries.append(msg)

        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", _track_log,
        )

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_completed.id)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "state").mkdir(exist_ok=True)
        (workspace / "state" / "state.json").write_text("{}")

        # First call — must log "Tearing down workspace" and remove directory
        orchestrator._teardown_workspace(pipeline_completed)
        assert not workspace.exists()
        assert log_entries.count("Tearing down workspace") == 1

        # Recreate the workspace to simulate a scenario where teardown
        # is called again (e.g. partial failure, race, re-entry).
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "state").mkdir(exist_ok=True)
        (workspace / "state" / "state.json").write_text("{}")

        # Second call — must be a no-op (immediate exit), no log, no removal
        orchestrator._teardown_workspace(pipeline_completed)

        assert log_entries.count("Tearing down workspace") == 1, (
            f"Expected exactly 1 'Tearing down workspace' log entry, "
            f"got {log_entries.count('Tearing down workspace')}: {log_entries}"
        )
        # The workspace should NOT have been removed by the second call
        assert workspace.exists(), (
            "Workspace should still exist after the second call — "
            "it should have been a no-op"
        )


class TestSymlinks:
    def test_symlinks_preserved(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
    ) -> None:
        """Symlinks in source are preserved in copy."""
        import tempfile

        control_dir = Path(patched_copy_sources["control"])
        target = control_dir / "README.md"
        link_path = control_dir / "link-to-readme"
        link_path.symlink_to(target)

        try:
            orchestrator._create_workspace(pipeline_queued)

            workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
            dest_link = workspace / "copies" / "etc" / "Wywy-Website-Control" / "link-to-readme"
            assert dest_link.is_symlink()
            resolved = dest_link.readlink()
            assert resolved.name == "README.md"
        finally:
            link_path.unlink(missing_ok=True)


class TestPipelineExecutionResilience:
    def test_execute_pipeline_fails_on_non_oserror(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """When a non-OSError exception escapes the try block,
        _execute_pipeline must catch it, log at CRITICAL level,
        and fail the pipeline gracefully.

        Several code paths inside the try block can raise non-OSError
        exceptions:

        * ``_start_opencode_server`` → ``docker.errors.DockerException``
          (daemon unreachable, image not found, API error).
        * ``_start_opencode_server`` → ``RuntimeError`` if a server
          container already exists for this pipeline (line 732).
        * ``_wait_for_server_health`` → ``RuntimeError`` if the server
          never becomes healthy (line 799).
        * ``_create_workspace`` → ``subprocess.TimeoutExpired`` if the
          git branch creation ``subprocess.run`` times out (line 541).

        Currently _execute_pipeline only catches ``OSError``, so any of
        these propagates to the orchestrator loop's catch-all
        ``except Exception``, which logs but does NOT set the pipeline
        status to "failed".  The pipeline is left in ``'running'`` state
        without any PipelineStage rows, causing the "Pipeline stage row
        missing for expected stage 'init'" cascade on the next tick.

        The fix: catch ``Exception`` instead of ``OSError``, log at
        ``CRITICAL`` level, and fail the pipeline.

        Regression test for: Pipeline stage row missing after workspace
        copy failure.
        """
        from apps.orchestrator.orchestrator import _execute_pipeline

        log_entries: list[tuple[str, str]] = []

        def _track_log(pipeline: Pipeline, level: str, msg: str, **kwargs) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        def _failing_create_workspace(pipeline: Pipeline) -> None:
            raise RuntimeError("Unexpected non-OSError failure")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._create_workspace",
            _failing_create_workspace,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        # The fix must catch the RuntimeError, log it at CRITICAL level,
        # and fail the pipeline — no exception should propagate.
        _execute_pipeline(pipeline_queued)

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed", (
            "Pipeline must be set to 'failed' even when _create_workspace "
            f"raises a non-OSError. Got: {pipeline_queued.status}"
        )

        assert len(log_entries) >= 2, (
            "Expected at least two orchestrator log entries "
            f"(CRITICAL + Pipeline ended). Got: {len(log_entries)}"
        )
        level, msg = log_entries[-2]
        assert level == "CRITICAL", (
            f"Expected CRITICAL log level for non-OSError failures. "
            f"Got: {level}"
        )
        assert "RuntimeError" in msg or "Unexpected non-OSError failure" in msg, (
            f"Log message must describe the failure. Got: {msg}"
        )
        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )

    def test_execute_pipeline_fatal_stage_creation_error_stays_dead(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        patched_copy_sources: dict[str, str],
    ) -> None:
        """When _create_stages raises after workspace setup, the pipeline
        MUST NOT remain in 'running' status.

        The try/except in _execute_pipeline covers only the workspace
        creation phase (_create_workspace, _start_opencode_server,
        _wait_for_server_health).  _create_stages and advance_pipeline
        are OUTSIDE the try block.  If either raises, the exception
        propagates out of _execute_pipeline, leaving the pipeline in
        ``'running'`` status.

        On the next orchestrator tick the pipeline is found as
        ``status="running"`` → the loop calls advance_pipeline → the
        stage row does not exist (because _create_stages never completed)
        → ``"Pipeline stage row missing for expected stage 'init'"``
        → the pipeline is finally set to ``'failed'`` one tick too late.

        During that extra tick the opencode server is running, the
        pipeline is in an inconsistent state (no stage rows), and any
        monitoring or UI that watches ``status="running"`` pipelines
        sees a zombie.

        RED: This test proves the gap.  _execute_pipeline currently
        lets the exception fly, the pipeline stays ``'running'``, and
        the assertion ``status != "running"`` fails.

        See orchestrator.py:239-268 — the workspace-creation try/except
        must also guard _create_stages and advance_pipeline.
        """
        from apps.orchestrator.orchestrator import _execute_pipeline

        # ── Arrange: pipeline in the state the orchestrator loop
        #    would have set before calling _execute_pipeline ──
        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        def _failing_create_stages(pipeline: Pipeline) -> None:
            raise RuntimeError(
                "Simulated unrecoverable failure in _create_stages"
            )

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._create_stages",
            _failing_create_stages,
        )

        # ── Act ──
        # _execute_pipeline currently does not catch this exception.
        # We catch it here so we can inspect the pipeline state
        # after the failed call.
        try:
            _execute_pipeline(pipeline_queued)
        except RuntimeError:
            pass

        # ── Assert ──
        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status != "running", (
            "RED: Pipeline MUST NOT remain 'running' after "
            "_create_stages fails. The orchestrator loop would "
            "retry it on the next tick, causing a zombie pipeline "
            "that cycles through 'Pipeline stage row missing for "
            "expected stage' forever."
        )

class TestOpenCodeConfig:
    def test_opencode_config_no_chown(
        self,
        pipeline_queued: Pipeline,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """opencode.json written without chown."""
        def _fail_chown(*args, **kwargs):
            raise AssertionError("os.chown should not be called")

        monkeypatch.setattr(os, "chown", _fail_chown)
        orchestrator._create_workspace(pipeline_queued)

        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_queued.id)
        config_path = workspace / ".opencode" / "opencode.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert config.get("permission") == "allow", (
            f"Expected permission='allow', got '{config.get('permission')}'"
        )


class TestCopytreeErrorPropagation:
    def test_copytree_permission_error_propagates(
        self, pipeline_queued, temp_workspace, temp_log_root, monkeypatch,
    ):
        """When copytree fails with PermissionError, _create_workspace must raise.

        Regression test: the dev container (uid 25230) cannot read source dirs
        owned by uid 1000 with 750 permissions.  Before the fix,
        _create_workspace silently caught OSError inside its copy loop and
        returned successfully -- leaving an empty copies/ tree.  The agent
        containers would then fail because they had no source code to work
        with.
        """
        import shutil as shutil_mod

        def _failing_copytree(src, dst, **kwargs):
            raise PermissionError(
                f"[Errno 13] Permission denied: '{src}'"
            )

        monkeypatch.setattr(shutil_mod, "copytree", _failing_copytree)

        with pytest.raises(PermissionError):
            orchestrator._create_workspace(pipeline_queued)


class TestLockedSecrets:
    def test_create_workspace_completes_despite_locked_secrets(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Copy completes and produces at least one copied entry despite
        an inaccessible secrets/ directory being present inside the source.

        _copy_source_tree passes an ``ignore`` callback to
        shutil.copytree that filters out ``secrets/`` before the walk
        descends into it, so the PermissionError on the locked dir is
        never raised.
        """
        src = tmp_path / "Control"
        src.mkdir()
        (src / "AGENTS.md").write_text("hello")
        (src / "config").mkdir()
        (src / "config" / "settings.yaml").write_text("key: val")

        locked = src / "secrets"
        locked.mkdir()
        (locked / "key.txt").write_text("secret!")
        locked.chmod(0o000)

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(src)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        try:
            orchestrator._create_workspace(pipeline_queued)

            copies = (
                Path(settings.WORKSPACE_ROOT)
                / str(pipeline_queued.id)
                / "copies"
                / "Control"
            )
            assert copies.exists()
            assert len(list(copies.iterdir())) > 0, (
                "At least one file or directory must have been copied"
            )
        finally:
            locked.chmod(0o755)


class TestLockedNonSecrets:
    def test_locked_non_secrets_dir_causes_failure(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A locked directory NOT named 'secrets' must cause the pipeline to fail.

        Only ``secrets/`` is a known, expected inaccessible directory that
        the copy must silently skip.  Any other locked subdirectory is a
        defect in the filesystem permissions strategy — the install scripts
        should have ensured all source trees are readable by group 2523.
        Silently skipping such a directory would hide a real problem.

        Regression test: _create_workspace must raise when it encounters an
        inaccessible directory that the _ignore_fn does not filter.
        """
        src = tmp_path / "Control"
        src.mkdir()
        (src / "AGENTS.md").write_text("hello")
        (src / "config").mkdir()
        (src / "config" / "settings.yaml").write_text("key: val")

        locked = src / "credentials"
        locked.mkdir()
        (locked / "token.txt").write_text("secret!")
        locked.chmod(0o000)

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(src)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        try:
            with pytest.raises(Exception):
                orchestrator._create_workspace(pipeline_queued)
        finally:
            locked.chmod(0o755)


class TestTeardownLogOrder:
    """Teardown logs must always be the final orchestrator log entry for a pipeline."""

    def test_teardown_log_is_final_on_execute_pipeline_workspace_failure(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When _execute_pipeline fails during workspace creation, the
        "Tearing down workspace" log must be the FINAL orchestrator
        log entry — not followed by a CRITICAL error.

        Current bug: in _execute_pipeline's except handler (orchestrator.py:251-286),
        _teardown_workspace is called BEFORE _write_orchestrator_log for
        the "Pipeline execution failed" CRITICAL message.

        This means operators see::

            INFO   Tearing down workspace
            CRITICAL  Failed to create workspace: ...

        The correct order must be::

            CRITICAL  Failed to create workspace: ...
            INFO   Tearing down workspace

        Because teardown is the terminal operation — nothing should log
        after the workspace has been cleaned up.
        """
        import shutil as shutil_mod
        from apps.orchestrator.orchestrator import _execute_pipeline

        log_entries: list[tuple[str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: object,
        ) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        # Create a minimal source tree so _create_workspace succeeds
        # up to the copytree call
        source1 = tmp_path / "source"
        source1.mkdir()
        (source1 / "file.txt").write_text("hello")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(source1)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        # Patch shutil.copytree to fail — this means _create_workspace
        # will create the directory structure, then fail during copytree,
        # so the workspace dir EXISTS when _teardown_workspace runs.
        def _failing_copytree(src: str, dst: str, **kwargs: object) -> None:
            raise PermissionError("Simulated copytree failure")

        monkeypatch.setattr(shutil_mod, "copytree", _failing_copytree)

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        # _execute_pipeline expects the pipeline to already be "running".
        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        _execute_pipeline(pipeline_queued)

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed"

        assert len(log_entries) >= 3, (
            f"Expected at least 3 log entries (CRITICAL + Teardown + "
            f"Pipeline ended), got {len(log_entries)}: {log_entries}"
        )

        # "Tearing down workspace" must be second-to-last — the only
        # thing after it is the benign "Pipeline ended" notification.
        second_last_level, second_last_msg = log_entries[-2]
        assert second_last_msg == "Tearing down workspace", (
            f"'Tearing down workspace' must be the second-to-last entry, "
            f"but got '{second_last_msg}' (level={second_last_level}). "
            f"Full log: {log_entries}"
        )

        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )

        # Verify the CRITICAL error appears BEFORE teardown
        criticals = [(lvl, msg) for lvl, msg in log_entries if lvl == "CRITICAL"]
        assert len(criticals) >= 1, (
            f"Expected at least 1 CRITICAL log entry, "
            f"got {len(criticals)}: {criticals}"
        )
        crit_idx = next(
            i for i, (lvl, _) in enumerate(log_entries) if lvl == "CRITICAL"
        )
        teardown_idx = next(
            i for i, (_, msg) in enumerate(log_entries)
            if msg == "Tearing down workspace"
        )
        assert crit_idx < teardown_idx, (
            f"CRITICAL log (at index {crit_idx}) must appear BEFORE "
            f"'Tearing down workspace' (at index {teardown_idx}). "
            f"Full log: {log_entries}"
        )

    def test_teardown_log_is_final_on_execute_pipeline_failure(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Same invariant via _execute_pipeline: "Tearing down workspace"
        must be the final log when workspace creation fails.

        _execute_pipeline (orchestrator.py:251-286) has the same
        ordering issue — _teardown_workspace is called before
        the CRITICAL "Pipeline execution failed" log.
        """
        import shutil as shutil_mod
        from apps.orchestrator.orchestrator import _execute_pipeline

        log_entries: list[tuple[str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: object,
        ) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        source1 = tmp_path / "source"
        source1.mkdir()
        (source1 / "file.txt").write_text("hello")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(source1)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        def _failing_copytree(src: str, dst: str, **kwargs: object) -> None:
            raise PermissionError("Simulated copytree failure")

        monkeypatch.setattr(shutil_mod, "copytree", _failing_copytree)

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        _execute_pipeline(pipeline_queued)

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed"

        assert len(log_entries) >= 3, (
            f"Expected at least 3 log entries (CRITICAL + Teardown + "
            f"Pipeline ended), got {len(log_entries)}: {log_entries}"
        )

        # "Tearing down workspace" must be second-to-last — the only
        # thing after it is the benign "Pipeline ended" notification.
        second_last_level, second_last_msg = log_entries[-2]
        assert second_last_msg == "Tearing down workspace", (
            f"'Tearing down workspace' must be the second-to-last entry, "
            f"but got '{second_last_msg}' (level={second_last_level}). "
            f"Full log: {log_entries}"
        )

        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )

        criticals = [(lvl, msg) for lvl, msg in log_entries if lvl == "CRITICAL"]
        assert len(criticals) >= 1
        crit_idx = next(
            i for i, (lvl, _) in enumerate(log_entries) if lvl == "CRITICAL"
        )
        teardown_idx = next(
            i for i, (_, msg) in enumerate(log_entries)
            if msg == "Tearing down workspace"
        )
        assert crit_idx < teardown_idx, (
            f"CRITICAL log (at index {crit_idx}) must appear BEFORE "
            f"'Tearing down workspace' (at index {teardown_idx}). "
            f"Full log: {log_entries}"
        )

    def test_teardown_log_is_final_on_init_stage_failure(
        self,
        pipeline_queued: Pipeline,
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Same invariant via _run_stage init path: "Tearing down workspace"
        must be the final log when workspace creation fails during the
        init stage.

        _run_stage's init handler (orchestrator.py:342-353) has the
        identical bug — _teardown_workspace is called before the CRITICAL
        "Failed to create workspace during init stage" log.
        """
        import shutil as shutil_mod
        from apps.orchestrator.orchestrator import advance_pipeline

        log_entries: list[tuple[str, str]] = []

        def _track_log(
            pipeline: Pipeline, level: str, msg: str, **kwargs: object,
        ) -> None:
            log_entries.append((level, msg))

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._write_orchestrator_log",
            _track_log,
        )

        # ── Set up pipeline ready for advance_pipeline to dispatch
        #    to _run_stage for the "init" stage.
        pipeline_queued.status = "running"
        pipeline_queued.save(update_fields=["status"])

        from apps.orchestrator.models import PipelineStage
        PipelineStage.objects.create(
            pipeline=pipeline_queued,
            name="init",
            status="pending",
        )

        source1 = tmp_path / "source"
        source1.mkdir()
        (source1 / "file.txt").write_text("hello")

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator.COPY_SOURCES",
            [str(source1)],
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._COPY_SOURCES_BASE",
            str(tmp_path),
        )

        def _failing_copytree(src: str, dst: str, **kwargs: object) -> None:
            raise PermissionError("Simulated copytree failure")

        monkeypatch.setattr(shutil_mod, "copytree", _failing_copytree)

        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._start_opencode_server",
            lambda p: None,
        )
        monkeypatch.setattr(
            "apps.orchestrator.orchestrator._wait_for_server_health",
            lambda p: None,
        )

        advance_pipeline(pipeline_queued)

        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "failed"

        assert len(log_entries) >= 3, (
            f"Expected at least 3 log entries (CRITICAL + Teardown + "
            f"Pipeline ended), got {len(log_entries)}: {log_entries}"
        )

        # "Tearing down workspace" must be second-to-last — the only
        # thing after it is the benign "Pipeline ended" notification.
        second_last_level, second_last_msg = log_entries[-2]
        assert second_last_msg == "Tearing down workspace", (
            f"'Tearing down workspace' must be the second-to-last entry, "
            f"but got '{second_last_msg}' (level={second_last_level}). "
            f"Full log: {log_entries}"
        )

        # Pipeline ended must be the FINAL orchestrator entry.
        final_level, final_msg = log_entries[-1]
        assert final_level == "INFO", (
            f"Final orchestrator entry must be INFO level, "
            f"got: {final_level}"
        )
        assert final_msg == "Pipeline ended (status=failed)", (
            f"The final log entry must be 'Pipeline ended (status=failed)', "
            f"got: '{final_msg}'"
        )

        criticals = [(lvl, msg) for lvl, msg in log_entries if lvl == "CRITICAL"]
        assert len(criticals) >= 1
        crit_idx = next(
            i for i, (lvl, _) in enumerate(log_entries) if lvl == "CRITICAL"
        )
        teardown_idx = next(
            i for i, (_, msg) in enumerate(log_entries)
            if msg == "Tearing down workspace"
        )
        assert crit_idx < teardown_idx, (
            f"CRITICAL log (at index {crit_idx}) must appear BEFORE "
            f"'Tearing down workspace' (at index {teardown_idx}). "
            f"Full log: {log_entries}"
        )
