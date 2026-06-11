"""Orchestrator thread loop - pipeline lifecycle, container management, git operations."""

import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

import docker
from docker.models.containers import Container
from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.orchestrator.models import Pipeline, PipelineStage

logger = logging.getLogger(__name__)

class RepoConfig(TypedDict):
    name: str
    url: str
    mount: str


STAGE_ORDER: list[str] = [
    "planner",
    "plan_reviewer",
    "test_builder",
    "testing_align_red",
    "coder",
    "code_reviewer",
    "testing_green",
    "pr_writer",
    "pr_reviewer",
]

REPO_CONFIG: list[RepoConfig] = [
    {
        "name": "Wywy-Website-Control",
        "url": "https://github.com/WywySenarios/Wywy-Website-Control.git",
        "mount": "/etc/Wywy-Website-Control",
    },
    {
        "name": "Wywy-Website",
        "url": "https://github.com/WywySenarios/Wywy-Website.git",
        "mount": "/usr/local/Wywy-Website/Wywy-Website",
    },
    {
        "name": "Wywy-Website-Cache",
        "url": "https://github.com/WywySenarios/Wywy-Website-Cache.git",
        "mount": "/usr/local/Wywy-Website/Wywy-Website-Cache",
    },
    {
        "name": "Wywy-Website-Master-Database",
        "url": "https://github.com/WywySenarios/Wywy-Website-Master-Database.git",
        "mount": "/usr/local/Wywy-Website/Wywy-Website-Master-Database",
    },
    {
        "name": "Wywy-Website-Backup",
        "url": "https://github.com/WywySenarios/Wywy-Website-Backup.git",
        "mount": "/usr/local/Wywy-Website/Wywy-Website-Backup",
    },
]

COPY_SOURCES: list[str] = [
    "/etc/Wywy-Website-Control",
    "/usr/local/Wywy-Website",
]

# Base path for computing relative copy destinations.  Defaults to "/" so
# that source.lstrip("/") == relpath(source, "/").  Tests may monkeypatch
# this to a temporary directory that mirrors the production layout.
_COPY_SOURCES_BASE: str = "/"

MIN_DISK_SPACE_GB: int = 10

_wake_event = threading.Event()


def _previous_stage_name(stage_name: str) -> str:
    """Return the name of the stage that precedes the given stage."""
    try:
        idx = STAGE_ORDER.index(stage_name)
        if idx > 0:
            return STAGE_ORDER[idx - 1]
    except ValueError:
        pass
    return ""


def wake_orchestrator() -> None:
    """Signal the orchestrator thread to check the queue immediately."""
    _wake_event.set()


def orchestrator_loop() -> None:
    """Main orchestrator thread loop - manages pipeline lifecycle."""
    logger.info("Orchestrator loop started")
    _ensure_agent_network()
    _reap_orphaned_pipelines()

    while True:
        try:
            pipeline_to_advance = None
            pipeline_to_start = None
            with transaction.atomic():
                active = (
                    Pipeline.objects
                    .select_for_update()
                    .filter(status="running")
                    .first()
                )
                if active:
                    pipeline_to_advance = active
                else:
                    next_pipeline = (
                        Pipeline.objects
                        .select_for_update()
                        .filter(status="queued")
                        .order_by("created_at")
                        .first()
                    )
                    if next_pipeline:
                        next_pipeline.status = "running"
                        next_pipeline.save(update_fields=["status", "updated_at"])
                        pipeline_to_start = next_pipeline

            if pipeline_to_advance:
                advance_pipeline(pipeline_to_advance)
            elif pipeline_to_start:
                _execute_pipeline(pipeline_to_start)
        except Exception:
            logger.exception("Orchestrator loop error")
        _wake_event.wait(timeout=1)
        _wake_event.clear()


def _ensure_agent_network() -> None:
    """Create the agent bridge network if it doesn't already exist."""
    try:
        client = docker.from_env()
        network_name = settings.AGENT_NETWORK
        try:
            client.networks.get(network_name)
        except docker.errors.NotFound:
            client.networks.create(network_name, driver="bridge")
            logger.info("Created agent network: %s", network_name)
    except docker.errors.DockerException:
        logger.exception("Failed to ensure agent network")


def _reap_orphaned_pipelines() -> None:
    """Transition any pre-existing 'running' pipelines to 'failed'.

    The orchestrator is single-threaded and can only advance one pipeline
    at a time.  Any pipeline still marked 'running' at thread start must
    have been orphaned by a previous crash — reset them so they don't
    block the queue forever.
    """
    orphaned = Pipeline.objects.filter(status="running")
    count = orphaned.count()
    if not count:
        return
    logger.warning("Reaping %d orphaned pipeline(s) from previous run", count)
    for pipeline in orphaned:
        pipeline.status = "failed"
        pipeline.save(update_fields=["status", "updated_at"])
        pipeline.stages.filter(status="running").update(status="failed")
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            "Pipeline orphaned by orchestrator restart — reset to failed",
        )


def start_pipeline(pipeline: Pipeline) -> None:
    """Initialize a queued pipeline: create workspace, stages, and launch first stage."""
    logger.info(
        "Starting pipeline %s (%s)",
        pipeline.id,
        pipeline.invocation_name,
    )

    try:
        _create_workspace(pipeline)
    except OSError as exc:
        _teardown_workspace(pipeline)
        pipeline.status = "failed"
        pipeline.save(update_fields=["status", "updated_at"])
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Failed to create workspace: {exc}",
        )
        return

    pipeline.status = "running"
    pipeline.save(update_fields=["status", "updated_at"])

    _create_stages(pipeline)
    _write_orchestrator_log(
        pipeline,
        "INFO",
        f"Pipeline started: {pipeline.invocation_name}",
    )
    advance_pipeline(pipeline)


def _execute_pipeline(pipeline: Pipeline) -> None:
    """Create workspace and stages for a pipeline already marked 'running'."""
    logger.info(
        "Executing pipeline %s (%s)",
        pipeline.id,
        pipeline.invocation_name,
    )

    try:
        _create_workspace(pipeline)
    except OSError as exc:
        _teardown_workspace(pipeline)
        pipeline.status = "failed"
        pipeline.save(update_fields=["status", "updated_at"])
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Failed to create workspace: {exc}",
        )
        return

    _create_stages(pipeline)
    _write_orchestrator_log(
        pipeline,
        "INFO",
        f"Pipeline started: {pipeline.invocation_name}",
    )
    advance_pipeline(pipeline)


def advance_pipeline(pipeline: Pipeline) -> None:
    """Advance a running pipeline to its next stage or mark it completed."""
    current_stage = pipeline.current_stage

    if not current_stage:
        next_stage_name = STAGE_ORDER[0]
    else:
        # Guard: refuse to advance when the current stage hasn't
        # reached a terminal state.  Protects against inconsistent
        # DB state if handle_stage_failure's saves partially fail.
        try:
            current_obj = pipeline.stages.get(name=current_stage)
            if current_obj.status not in ("completed", "blocked"):
                return
        except PipelineStage.DoesNotExist:
            pass

        try:
            idx = STAGE_ORDER.index(current_stage)
            next_stage_name = STAGE_ORDER[idx + 1]
        except (ValueError, IndexError):
            _complete_pipeline(pipeline)
            return

    stage = pipeline.stages.get(name=next_stage_name)
    if stage.status in ("completed", "failed"):
        return
    if stage.retry_after and stage.retry_after > dj_timezone.now():
        return

    _run_stage(pipeline, stage)


def _run_stage(pipeline: Pipeline, stage: PipelineStage) -> None:
    """Execute a single pipeline stage by spawning an agent container."""
    pipeline.current_stage = stage.name
    pipeline.save(update_fields=["current_stage", "updated_at"])

    stage.status = "running"
    stage.started_at = dj_timezone.now()
    stage.retry_after = None
    stage.save(update_fields=["status", "started_at", "retry_after"])

    _write_orchestrator_log(
        pipeline,
        "INFO",
        f"Stage {stage.name} starting (retry {stage.retry_count})",
    )

    state_file = _state_file_path(pipeline)
    _write_state_field(state_file, "current_stage", stage.name)

    try:
        exit_code, blocked = _spawn_agent_container(pipeline, stage)
    except Exception:
        logger.exception("Failed to spawn agent container for stage %s", stage.name)
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Container spawn failed for stage {stage.name}",
        )
        _handle_stage_failure(pipeline, stage)
        return

    stage.finished_at = dj_timezone.now()
    duration = (stage.finished_at - stage.started_at).total_seconds()

    if blocked:
        stage.status = "blocked"
        pipeline.user_input_pending = True
        stage.save(update_fields=["status", "finished_at"])
        pipeline.save(update_fields=["user_input_pending", "updated_at"])
        _write_orchestrator_log(
            pipeline,
            "INFO",
            f"Stage {stage.name} blocked - awaiting user input",
        )
        return

    if exit_code == 0:
        valid, reason = _validate_stage_state(pipeline, stage)
        if valid:
            stage.status = "completed"
            stage.save(update_fields=["status", "finished_at"])
            _write_orchestrator_log(
                pipeline,
                "INFO",
                f"Stage {stage.name} completed in {duration:.1f}s",
            )
            if stage.name == "coder":
                _run_formatters(pipeline)
            advance_pipeline(pipeline)
        else:
            stage.status = "failed"
            stage.save(update_fields=["status", "finished_at"])
            _write_orchestrator_log(
                pipeline,
                "ERROR",
                f"Stage {stage.name} state validation failed: {reason}",
            )
            _handle_stage_failure(pipeline, stage)
    else:
        _write_orchestrator_log(
            pipeline,
            "WARN",
            f"Stage {stage.name} exited with code {exit_code}",
        )
        _handle_stage_failure(pipeline, stage)


def _handle_stage_failure(pipeline: Pipeline, stage: PipelineStage) -> None:
    """Handle a failed stage with retry logic (non-blocking)."""
    stage.retry_count += 1

    if stage.retry_count > settings.PIPELINE_MAX_RETRIES:
        stage.status = "failed"
        pipeline.status = "failed"
        with transaction.atomic():
            stage.save(update_fields=["status", "retry_count"])
            pipeline.save(update_fields=["status", "updated_at"])
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Stage {stage.name} failed after {stage.retry_count} retries",
        )
        return

    backoff_idx = min(stage.retry_count - 1, len(settings.PIPELINE_RETRY_BACKOFF_SECONDS) - 1)
    delay = settings.PIPELINE_RETRY_BACKOFF_SECONDS[backoff_idx]
    stage.status = "pending"
    stage.retry_after = dj_timezone.now() + dj_timezone.timedelta(seconds=delay)
    prev = _previous_stage_name(stage.name)
    pipeline.current_stage = prev if prev else None
    with transaction.atomic():
        stage.save(update_fields=["status", "retry_count", "retry_after"])
        pipeline.save(update_fields=["current_stage", "updated_at"])
    _write_orchestrator_log(
        pipeline,
        "WARN",
        f"Stage {stage.name} retry {stage.retry_count}/{settings.PIPELINE_MAX_RETRIES} in {delay}s",
    )


def _complete_pipeline(pipeline: Pipeline) -> None:
    """Mark a pipeline as completed and attempt PR creation."""
    pipeline.status = "completed"
    pipeline.save(update_fields=["status", "updated_at"])
    _write_orchestrator_log(pipeline, "INFO", "Pipeline completed")
    try:
        _create_pr(pipeline)
    except Exception:
        logger.exception("PR creation failed for pipeline %s", pipeline.id)
    _teardown_workspace(pipeline)


def _teardown_workspace(pipeline: Pipeline) -> None:
    """Remove workspace directory after pipeline completion or cancellation.

    Logs survive independently at /var/log/Wywy-Website/agentic/{pipeline_id}/.
    """
    workspace_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    if workspace_dir.exists():
        _write_orchestrator_log(pipeline, "INFO", "Tearing down workspace")
        shutil.rmtree(str(workspace_dir), ignore_errors=True)


def _check_disk_space(workspace_root: str) -> None:
    """Check that at least MIN_DISK_SPACE_GB is available on the workspace filesystem.

    Raises:
        OSError: If insufficient disk space is available.
    """
    stat = os.statvfs(workspace_root)
    free_bytes = stat.f_bavail * stat.f_frsize
    free_gb = free_bytes / (1024**3)
    if free_gb < MIN_DISK_SPACE_GB:
        raise OSError(
            f"Insufficient disk space: {free_gb:.1f}GB available, "
            f"{MIN_DISK_SPACE_GB}GB required"
        )


def _create_workspace(pipeline: Pipeline) -> None:
    """Create workspace directory structure, copy source trees, and initialize state."""
    workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    copies_dir = workspace / "copies"
    state_dir = workspace / "state"
    artifacts_dir = workspace / "artifacts"
    context_dir = workspace / "context"

    _check_disk_space(settings.WORKSPACE_ROOT)

    for dir_ in [copies_dir, state_dir, artifacts_dir, context_dir]:
        dir_.mkdir(parents=True, exist_ok=True)

    (context_dir / "user-input").mkdir(parents=True, exist_ok=True)

    log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)
    log_dir.mkdir(parents=True, exist_ok=True)

    _write_opencode_config(workspace)

    for source in COPY_SOURCES:
        rel_path = os.path.relpath(source, _COPY_SOURCES_BASE)
        dest = copies_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_orchestrator_log(pipeline, "INFO", f"Copying {source}...")
        shutil.copytree(
            source, str(dest),
            symlinks=True,
            dirs_exist_ok=False,
        )

    for repo in REPO_CONFIG:
        repo_path = copies_dir / repo["mount"].lstrip("/")
        if not repo_path.exists():
            _write_orchestrator_log(
                pipeline, "WARN",
                f"Repo {repo['name']} not found at {repo_path}, skipping branch creation",
            )
            continue
        try:
            subprocess.run(
                ["git", "checkout", "-b", pipeline.invocation_name],
                cwd=str(repo_path),
                capture_output=True, text=True, timeout=30,
                check=True,
            )
            _write_orchestrator_log(
                pipeline, "INFO",
                f"Branch {pipeline.invocation_name} created in {repo['name']}",
            )
        except subprocess.CalledProcessError as exc:
            _write_orchestrator_log(
                pipeline, "WARN",
                f"Branch creation failed for {repo['name']}: {exc.stderr.strip() if exc.stderr else 'unknown error'}",
            )

    _init_state_file(pipeline)


def _write_opencode_config(workspace: Path) -> None:
    """Write .opencode/opencode.json disabling webfetch for the agent container."""
    config_dir = workspace / ".opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "permissions": {
            "deny": ["webfetch"],
        },
    }
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))


def _init_state_file(pipeline: Pipeline) -> None:
    """Create the initial state.json for a pipeline."""
    state_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id) / "state"
    workspace_root = f"{settings.WORKSPACE_ROOT}/{pipeline.id}"
    state = {
        "pipeline_id": str(pipeline.id),
        "status": "running",
        "current_stage": None,
        "iteration_count": 0,
        "user_input_pending": False,
        "workspace": {
            "root": workspace_root,
            "repos": {
                repo["name"]: f"{workspace_root}/copies/{repo['mount'].lstrip('/')}"
                for repo in REPO_CONFIG
            },
            "state": f"{workspace_root}/state",
            "artifacts": f"{workspace_root}/artifacts",
            "context": f"{workspace_root}/context",
        },
        "artifacts": {
            "plan": "artifacts/plan.md",
            "spec": "artifacts/spec.md",
            "tests": "artifacts/tests/",
            "pr_url": None,
        },
        "stages": {
            name: {"status": "pending", "output": None}
            for name in STAGE_ORDER
        },
        "errors": [],
        "logs": {
            "base_dir": f"{settings.LOG_ROOT}/{pipeline.id}/",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_fp = state_dir / "state.json"
    state_fp.write_text(json.dumps(state, indent=2))


def _state_file_path(pipeline: Pipeline) -> Path:
    return Path(settings.WORKSPACE_ROOT) / str(pipeline.id) / "state" / "state.json"


def _write_state_field(state_path: Path, key: str, value: object) -> None:
    """Atomically update a field in state.json."""
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    state[key] = value
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = Path(str(state_path) + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(state_path)


def _read_state_field(pipeline: Pipeline, key: str) -> object:
    """Read a single field from state.json."""
    state_path = _state_file_path(pipeline)
    try:
        state = json.loads(state_path.read_text())
        return state.get(key)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _validate_stage_state(pipeline: Pipeline, stage: PipelineStage) -> tuple[bool, str]:
    """Validate that the agent wrote valid state to state.json after exiting.

    Returns (valid, reason).
    """
    state = _read_state_field(pipeline, "stages")
    if not isinstance(state, dict):
        return False, "stages key is missing or not a dict in state.json"
    stage_state = state.get(stage.name)
    if not isinstance(stage_state, dict):
        return False, f"stage '{stage.name}' missing or not a dict (keys: {list(state.keys()) if isinstance(state, dict) else 'N/A'})"
    status = stage_state.get("status")
    if status not in ("completed", "blocked", "failed"):
        return False, f"stage '{stage.name}' has status '{status}' (expected completed|blocked|failed)"
    return True, ""


def _create_stages(pipeline: Pipeline) -> None:
    """Initialize all pipeline stage records."""
    for name in STAGE_ORDER:
        PipelineStage.objects.create(pipeline=pipeline, name=name)


def _spawn_agent_container(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
    """Start an agent container via Docker SDK and wait for completion.
    
    Returns (exit_code, is_blocked).
    """
    workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)

    volumes: dict[str, dict] = {}
    for repo in REPO_CONFIG:
        repo_path = workspace / "copies" / repo["mount"].lstrip("/")
        if repo_path.exists():
            volumes[str(repo_path)] = {"bind": repo["mount"], "mode": "rw"}
    volumes.update({
        str(workspace / "state"): {"bind": "/state", "mode": "rw"},
        str(workspace / "artifacts"): {"bind": "/artifacts", "mode": "rw"},
        str(workspace / "context"): {"bind": "/context", "mode": "rw"},
        str(workspace / ".opencode"): {"bind": "/workspace/.opencode", "mode": "ro"},
        str(log_dir): {"bind": "/logs", "mode": "rw"},
    })

    client = docker.from_env()
    container = client.containers.run(
        image=settings.AGENT_IMAGE,
        command=["opencode", "run", "--print-logs", f"Stage: {stage.name}. Write to /state/state.json to report your progress."],
        environment={
            "STAGE": stage.name,
            "PIPELINE_ID": str(pipeline.id),
            "BRANCH_NAME": pipeline.invocation_name,
            "HOME": "/home/wywy",
            "PREVIOUS_STAGE": _previous_stage_name(stage.name),
            "DEEPSEEK_API_KEY": getattr(settings, "AGENT_DEEPSEEK_API_KEY", ""),
            "OPENAI_API_KEY": getattr(settings, "AGENT_OPENAI_API_KEY", ""),
            "ANTHROPIC_API_KEY": getattr(settings, "AGENT_ANTHROPIC_API_KEY", ""),
            "OPENCODE_API_KEY": getattr(settings, "AGENT_OPENCODE_API_KEY", ""),
        },
        volumes=volumes,
        user=f":{settings.AGENT_CONTAINER_GID}",
        detach=True,
        network=settings.AGENT_NETWORK,
    )

    exit_code = 1
    try:
        result = container.wait(timeout=settings.PIPELINE_TIMEOUT_SECONDS)
        exit_code = result.get("StatusCode", 1)
    except docker.errors.DockerException as e:
        _write_orchestrator_log(
            pipeline, "ERROR",
            f"Container wait failed for {stage.name}: {e}",
        )
    finally:
        _capture_container_logs(pipeline, stage, container, exit_code)
        try:
            container.remove(force=True)
        except docker.errors.DockerException:
            logger.warning(
                "Failed to remove container %s for pipeline %s",
                container.short_id, pipeline.id,
            )

    if exit_code != 0:
        return exit_code, False

    is_blocked = _check_blocked_state(pipeline, stage)
    return exit_code, is_blocked


def _capture_container_logs(pipeline: Pipeline, stage: PipelineStage, container: Container, exit_code: int) -> None:
    """Capture container stdout/stderr and write to the pipeline log directory."""
    try:
        raw = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Failed to retrieve container logs for stage %s", stage.name)
        raw = "(failed to retrieve container logs)"

    log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stage.name}.log"
    try:
        with open(log_path, "a") as f:
            f.write(raw)
            if not raw.endswith("\n"):
                f.write("\n")
    except Exception:
        logger.warning(
            "Failed to write stage log %s for pipeline %s",
            log_path, pipeline.id,
        )

    if exit_code != 0:
        tail = raw[-2000:] if len(raw) > 2000 else raw
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Stage {stage.name} exited {exit_code}: {tail}",
        )


def _check_blocked_state(pipeline: Pipeline, stage: PipelineStage) -> bool:
    """Check state.json to determine if the stage is blocked on user input."""
    state = _read_state_field(pipeline, "stages")
    if isinstance(state, dict):
        stage_state = state.get(stage.name)
        if isinstance(stage_state, dict):
            if stage_state.get("status") == "blocked":
                output = stage_state.get("output")
                if isinstance(output, dict):
                    pipeline.user_input_request = output
                    pipeline.save(update_fields=["user_input_request"])
                return True
    return False


def _run_formatters(pipeline: Pipeline) -> None:
    """Run code formatters on workspace repos after Coder completes."""
    copies_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id) / "copies"
    if not copies_dir.exists():
        return

    _write_orchestrator_log(pipeline, "INFO", "Running formatters")
    for repo in REPO_CONFIG:
        repo_path = copies_dir / repo["mount"].lstrip("/")
        if not repo_path.exists():
            continue

        try:
            subprocess.run(
                ["ruff", "check", "--fix", str(repo_path)],
                capture_output=True, timeout=120,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("ruff formatter failed for %s", repo["name"])

        try:
            subprocess.run(
                ["prettier", "--write", f"{repo_path}/**/*.{{ts,tsx,js,jsx}}"],
                capture_output=True, timeout=120, shell=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("prettier formatter failed for %s", repo["name"])

        try:
            subprocess.run(
                ["clang-format", "-i", f"{repo_path}/**/*.{{c,h}}"],
                capture_output=True, timeout=120, shell=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("clang-format formatter failed for %s", repo["name"])


def _create_pr(pipeline: Pipeline) -> None:
    """Create a GitHub PR from the PR Writer's payload."""
    payload = None
    pr_writer_stage = pipeline.stages.filter(name="pr_writer").first()
    if pr_writer_stage and pr_writer_stage.output:
        payload = pr_writer_stage.output

    if not payload:
        logger.warning("No PR payload found for pipeline %s", pipeline.id)
        return

    token = _read_github_token()
    if not token:
        logger.error("GitHub token not found, cannot create PR")
        return

    repo_name = payload.get("repo", "Wywy-Website")
    pr_title = payload.get("title", f"Pipeline {pipeline.id}")
    pr_description = payload.get("description", "")
    branch_name = pipeline.invocation_name

    workspace_root = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    repo_path_override = None
    for repo in REPO_CONFIG:
        if repo["name"] == repo_name:
            repo_path_override = workspace_root / "copies" / repo["mount"].lstrip("/")
            break
    workspace = repo_path_override or workspace_root / "repos" / repo_name

    try:
        git_env = _build_subprocess_env({})
        subprocess.run(
            ["git", "checkout", branch_name],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
            env=git_env,
        )
        auth_env = _build_subprocess_env({"GITHUB_TOKEN": token})
        subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=60,
            env=auth_env,
        )
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", pr_title,
                "--body", pr_description,
                "--base", "main",
                "--head", branch_name,
            ],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=30,
            env=auth_env,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            pipeline.pr_url = pr_url
            pipeline.save(update_fields=["pr_url", "updated_at"])
            _write_orchestrator_log(
                pipeline, "INFO", f"PR created: {pr_url}",
            )
    except (subprocess.SubprocessError, OSError):
        _write_orchestrator_log(pipeline, "ERROR", "PR creation failed")
        logger.exception("PR creation failed for pipeline %s", pipeline.id)


def _read_github_token() -> Optional[str]:
    """Read GitHub token from the mounted secret file."""
    try:
        return Path(settings.GITHUB_TOKEN_FILE).read_text().strip()
    except (FileNotFoundError, PermissionError):
        return None


def _build_subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    """Build a minimal environment for subprocess calls, avoiding secret leakage.

    Only copies safe variables from ``os.environ`` and adds ``extra`` entries.
    Never passes API keys, the Django secret key, or internal config.
    """
    safe_vars = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL"}
    env: dict[str, str] = {}
    for var in safe_vars:
        if var in os.environ:
            env[var] = os.environ[var]
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra)
    return env


def write_user_input_response(pipeline: Pipeline, response: str) -> None:
    """Record user input response and restart the blocked stage."""
    pipeline.user_input_pending = False
    pipeline.user_input_response = response
    pipeline.save(update_fields=["user_input_pending", "user_input_response", "updated_at"])
    _write_orchestrator_log(pipeline, "INFO", "User input received, resuming pipeline")
    wake_orchestrator()


def abort_pipeline(pipeline: Pipeline) -> None:
    """Abort a pipeline, setting it to cancelled status."""
    pipeline.status = "cancelled"
    pipeline.save(update_fields=["status", "updated_at"])
    _write_orchestrator_log(pipeline, "INFO", "Pipeline aborted by user")
    _teardown_workspace(pipeline)
    wake_orchestrator()


def _write_orchestrator_log(pipeline: Pipeline, level: str, msg: str, ctx: Optional[dict] = None) -> None:
    """Write a structured log entry for the orchestrator."""
    log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "orchestrator.log"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "pipeline": str(pipeline.id),
        "stage": pipeline.current_stage or "-",
        "src": "orchestrator",
        "msg": msg,
        "ctx": ctx or {},
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.warning(
            "Failed to write orchestrator log entry for pipeline %s",
            pipeline.id,
        )
