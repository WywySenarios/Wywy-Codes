"""Orchestrator thread loop - pipeline lifecycle, container management, git operations."""

import json
import logging
import os
import select
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

import docker
import requests
from docker.models.containers import Container
from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError
from django.utils import timezone as dj_timezone

from apps.orchestrator.exceptions import (
    InitialStageAdvancementError,
    MissingInitialStageError,
    PipelineNotRunningError,
    StageAdvancementError,
    StageAlreadyTerminalError,
    StageNotFoundError,
    StageNotInOrderError,
)
from apps.orchestrator.models import Pipeline, PipelineStage

logger = logging.getLogger(__name__)

# Tracks pipelines whose workspace has been torn down.
# Prevents _teardown_workspace from logging and acting multiple times
# for the same pipeline. Keyed by str(pipeline.id).
_teardown_completed: set[str] = set()

class RepoConfig(TypedDict):
    name: str
    url: str
    mount: str


STAGE_ORDER: list[str] = [
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compilance",
    "PR writer",
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

PIPELINE_MAX_RETRIES: int = settings.PIPELINE_MAX_RETRIES

# Maximum time (seconds) to poll state.json for the agent's response.
# Default 30 s — agents typically write within a few seconds.  For longer
# operations the retry mechanism (backed by _try_recover_retry) catches
# late writes.  Tests may monkeypatch this to a small value to avoid
# blocking.
STATE_POLL_TIMEOUT: int = 30

# Interval (seconds) between state.json polls.
STATE_POLL_INTERVAL: float = 2.0

_wake_event = threading.Event()
_opencode_server_containers: dict[str, Container] = {}

# Path to the named pipe (FIFO) used for cross-process signaling between
# Gunicorn workers.  The orchestrator worker reads from this pipe; HTTP
# workers write wake/abort signals to it.
_SIGNAL_FIFO_PATH: str = "/tmp/orchestrator_signals.fifo"

# Cross-process abort signal store.  Populated by FIFO reads in the
# orchestrator loop, consumed by _handle_stage_failure and the
# queued-pipeline abort check.  Only the orchestrator worker
# (the one holding the file lock) accesses this set.
_pending_aborts: set[str] = set()

# Terminal pipeline statuses that must not be revived without explicit opt-in.
TERMINAL_STATUSES: frozenset[str] = frozenset({"failed", "cancelled", "completed"})
NON_TERMINAL_STATUSES: frozenset[str] = frozenset({"queued", "running"})


def _previous_stage_name(stage_name: str) -> str:
    """Return the name of the stage that precedes the given stage."""
    try:
        idx = STAGE_ORDER.index(stage_name)
        if idx > 0:
            return STAGE_ORDER[idx - 1]
    except ValueError:
        pass
    return ""


def _ensure_signal_fifo() -> None:
    """Create the cross-process signal FIFO if it does not exist.

    Idempotent — calling this multiple times is safe (mkfifo raises
    FileExistsError on subsequent calls, which is caught and ignored).
    """
    try:
        os.mkfifo(_SIGNAL_FIFO_PATH, 0o644)
    except FileExistsError:
        pass


def wake_orchestrator() -> None:
    """Signal the orchestrator thread to check for work immediately.

    Writes a ``"wake\\n"`` signal to the cross-process FIFO so that
    the orchestrator worker (which may be a different OS process) is
    notified.  Also sets the in-process ``_wake_event`` for the
    same-worker case.
    """
    _wake_event.set()
    try:
        fd = os.open(_SIGNAL_FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, b"wake\n")
        os.close(fd)
    except OSError:
        pass


def _read_fifo_signals(fifo_fd: int) -> None:
    """Non-blocking read of all pending signals from the FIFO.

    Parses each line and updates ``_pending_aborts`` accordingly.
    ``"wake"`` lines are implicit — ``select.select`` already returned
    because data was available.
    """
    try:
        data = os.read(fifo_fd, 4096)
        for line in data.decode().strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line == "wake":
                pass
            elif line.startswith("abort:"):
                _pending_aborts.add(line.split(":", 1)[1])
    except (BlockingIOError, OSError):
        pass


def orchestrator_loop() -> None:
    """Main orchestrator thread loop - manages pipeline lifecycle."""
    logger.info("Orchestrator loop started")
    _ensure_agent_network()
    _reap_orphaned_pipelines()

    _ensure_signal_fifo()
    fifo_fd = os.open(_SIGNAL_FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)

    while True:
        _read_fifo_signals(fifo_fd)

        try:
            pipeline_to_advance = None
            pipeline_to_start = None
            active = (
                Pipeline.objects
                .filter(status="running")
                .first()
            )
            if active:
                pipeline_to_advance = active
            else:
                next_pipeline = (
                    Pipeline.objects
                    .filter(status="queued")
                    .order_by("created_at")
                    .first()
                )
                if next_pipeline:
                    # Check if an abort was requested before it started
                    if str(next_pipeline.id) in _pending_aborts:
                        _pending_aborts.discard(str(next_pipeline.id))
                        _transition_pipeline_state(next_pipeline, "cancelled")
                        _write_orchestrator_log(
                            next_pipeline,
                            "INFO",
                            "Pipeline aborted by user before execution",
                        )
                        continue  # back to loop, next_pipeline already set=None
                    _transition_pipeline_state(next_pipeline, "running")
                    pipeline_to_start = next_pipeline

            if pipeline_to_advance:
                advance_pipeline(pipeline_to_advance)
            elif pipeline_to_start:
                _execute_pipeline(pipeline_to_start)
        except Exception:
            logger.exception("Orchestrator loop error")

        # Non-blocking test hook: monkeypatched in tests to raise
        # ``BreakLoop`` and exit the ``while True``.  In production
        # this is a no-op (returns ``False`` immediately since the
        # event is never set from outside the loop).
        _wake_event.wait(0)

        # Block until a signal arrives on the FIFO or the timeout
        # expires, so we don't busy-loop.  The timeout (1 s) also
        # ensures we periodically re-check the while-condition.
        select.select([fifo_fd], [], [], 1.0)


def _ensure_agent_network() -> None:
    """Create the agent bridge network and connect the orchestrator container.

    Ensures that the agent Docker network exists, then connects the
    orchestrator's own container (via ``socket.gethostname()``) to that
    network so it can reach pipeline opencode server containers for health
    checks and stage execution.

    Docker's ``network.connect()`` is **not** idempotent — the Engine API
    returns ``403 Forbidden`` when the endpoint already exists in the
    network.  The broad ``except DockerException`` handler below catches
    this case so a re-connect does not crash the orchestrator, but it does
    produce a noisy ERROR-level traceback.

    Thread-safe (locked: GIL + the ``except DockerException`` handler
    converts harmless re-connect races into a log line rather than a
    crash).
    """
    try:
        client = docker.from_env()
        network_name = settings.AGENT_NETWORK
        try:
            network = client.networks.get(network_name)
        except docker.errors.NotFound:
            network = client.networks.create(network_name, driver="bridge")
            logger.info("Created agent network: %s", network_name)
        # Connect the orchestrator's own container so it can reach pipeline
        # containers on the agent network.
        container_id = socket.gethostname()
        network.connect(container_id)
    except docker.errors.DockerException:
        logger.exception("Failed to ensure agent network")


def _reap_orphaned_pipelines() -> None:
    """Transition any pre-existing 'running' pipelines to 'failed'.

    The orchestrator is single-threaded and can only advance one pipeline
    at a time.  Any pipeline still marked 'running' at thread start must
    have been orphaned by a previous crash — reset them so they don't
    block the queue forever.
    """
    try:
        orphaned = Pipeline.objects.filter(status="running")
        count = orphaned.count()
    except OperationalError:
        # Transient DB failures (mount/permissions) should not kill the
        # orchestrator thread loop.
        logger.warning("Skipping orphan reaping due to transient DB error")
        return
    if not count:
        return
    logger.warning("Reaping %d orphaned pipeline(s) from previous run", count)
    for pipeline in orphaned:
        _transition_pipeline_state(pipeline, "failed")
        pipeline.stages.filter(status="running").update(status="failed")
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            "Pipeline orphaned by orchestrator restart — reset to failed",
        )


def _execute_pipeline(pipeline: Pipeline) -> None:
    """Execute a pipeline from 'running' through the init stage into RED.

    Creates workspace, starts the opencode server, creates stages, runs
    the init stage via ``advance_pipeline``, and then transitions to RED
    via ``_run_stage``.  The init stage is left as ``"pending"`` so that
    the normal advancement guard in ``advance_pipeline`` never attempts
    to advance FROM init — the transition to RED is handled here inline.
    """
    logger.info(
        "Executing pipeline %s (%s)",
        pipeline.id,
        pipeline.invocation_name,
    )

    _write_orchestrator_log(
        pipeline,
        "INFO",
        "Pipeline execution started",
    )

    try:
        _create_workspace(pipeline)
        _start_opencode_server(pipeline)
        _wait_for_server_health(pipeline)
        _create_stages(pipeline)
    except Exception as exc:
        _transition_pipeline_state(pipeline, "failed")
        _write_orchestrator_log(
            pipeline,
            "CRITICAL",
            f"Pipeline execution failed: {exc}",
        )
        _teardown_workspace(pipeline)
        return

    _write_orchestrator_log(
        pipeline,
        "INFO",
        f"Pipeline started: {pipeline.invocation_name}",
    )
    advance_pipeline(pipeline)

    # After advance_pipeline runs the init stage, init stays
    # "pending" (not "completed"), so the normal advancement guard
    # in advance_pipeline will never advance past it.  We must
    # explicitly transition to the next stage here.
    if pipeline.current_stage == STAGE_ORDER[0]:
        try:
            next_stage = pipeline.stages.get(name=STAGE_ORDER[1])
        except PipelineStage.DoesNotExist:
            _write_orchestrator_log(
                pipeline,
                "CRITICAL",
                f"Next stage '{STAGE_ORDER[1]}' missing after init",
            )
            _transition_pipeline_state(
                pipeline,
                "failed",
                error_message=(
                    f"Stage row '{STAGE_ORDER[1]}' missing after init"
                ),
            )
            _teardown_workspace(pipeline)
            return
        _run_stage(pipeline, next_stage)


def _try_recover_retry(pipeline: Pipeline, stage: PipelineStage) -> bool:
    """Check state.json when a stage is in retry — the agent may have written
    a terminal status between orchestrator ticks.

    If ``state.json`` shows ``"completed"`` or ``"blocked"`` for the stage,
    update the DB record, set ``pipeline.current_stage``, and return ``True``.
    The next ``advance_pipeline`` tick will then advance to the following stage.

    Returns ``False`` if the agent still hasn't written a terminal status.
    """
    state = _read_state_field(pipeline, "stages")
    if not isinstance(state, dict):
        return False
    stage_state = state.get(stage.name)
    if not isinstance(stage_state, dict):
        return False
    status = stage_state.get("status")
    if status == "completed":
        stage.status = "completed"
        stage.retry_after = None
        stage.finished_at = dj_timezone.now()
        pipeline.current_stage = stage.name
        stage.save(update_fields=["status", "retry_after", "finished_at"])
        pipeline.save(update_fields=["current_stage", "updated_at"])
        return True
    if status == "blocked":
        stage.status = "blocked"
        stage.retry_after = None
        stage.finished_at = dj_timezone.now()
        pipeline.current_stage = stage.name
        pipeline.user_input_pending = True
        stage.save(update_fields=["status", "retry_after", "finished_at"])
        pipeline.save(update_fields=["current_stage", "user_input_pending", "updated_at"])
        return True
    return False


def _validate_stage_advancement(
    pipeline: Pipeline,
    stage: Optional[PipelineStage],
    *,
    force: bool = False,
    expected_initial_stage: Optional[str] = None,
) -> None:
    """Validate that advancing the pipeline to the given stage is legal.

    Checks pipeline status (must be ``'running'``), stage existence
    (must not be ``None``), initial-stage contract when the pipeline
    has no current stage, stage status (must not be terminal
    ``'completed'`` or ``'failed'``), and stage name validity (must
    be present in ``STAGE_ORDER``).  Raises a ``StageAdvancementError``
    subclass on the first violation.

    When *expected_initial_stage* is provided and the pipeline has no
    current stage (``current_stage is None``), the helper validates
    that the first advance targets the expected initial stage.  When
    the current stage *is* the expected initial stage, advancing to
    the next stage is forbidden — the initial stage is a setup boundary.

    .. note::
       The ``force`` parameter **must not** be used without consulting
       the project maintainers.  ``force=True`` bypasses all validation
       and can silently corrupt pipeline state when:

       * The terminal-status guard in ``_transition_pipeline_state`` is
         circumvented.
       * Stage-status invariants (e.g. re-executing a completed stage)
         are violated.
       * A non-orchestrator code path calls this function with
         ``force=True``, bypassing the single-orchestrator guarantee
         provided by the ``fcntl.flock`` election in
         ``OrchestratorConfig.ready()``.

    Parameters
    ----------
    pipeline
        The pipeline being advanced.
    stage
        The stage to advance.  ``None`` means no ``PipelineStage`` rows
        exist yet — e.g. a second orchestrator thread found the pipeline
        in the initialisation window after ``_transition_pipeline_state``
        but before ``_create_stages``.
    force
        Bypass all validation.  **Do not use** without consulting
        project maintainers (see above).
    expected_initial_stage
        The name of the stage that is expected to be the first stage
        when ``pipeline.current_stage`` is ``None``.  **Required**
        when ``current_stage`` is ``None`` — the helper raises
        ``StageAdvancementError`` if omitted.

    Raises
    ------
    StageAdvancementError
        Subclass: the specific reason the advancement was rejected.
    """
    if force:
        return

    # ── Guard 1: pipeline must be running ────────────────────────────────
    if pipeline.status != "running":
        raise PipelineNotRunningError(
            "Cannot advance stage on pipeline "
            f"with status '{pipeline.status}' — must be 'running'"
        )

    # ── Guard 2: stage must exist ────────────────────────────────────────
    if stage is None:
        if expected_initial_stage is not None and pipeline.current_stage is None:
            raise MissingInitialStageError(
                f"Cannot start pipeline: expected initial stage "
                f"'{expected_initial_stage}' not found — "
                f"no stage rows exist yet"
            )
        raise StageNotFoundError(
            "cannot advance None stage — no stage rows exist yet"
        )

    # ── Guard 3: expected_initial_stage required when no current stage ───
    # Must come after the stage-is-None check so that callers who forget
    # to pass expected_initial_stage with a None current_stage AND a
    # None stage get StageNotFoundError first (backward compatibility).
    if expected_initial_stage is None and pipeline.current_stage is None:
        raise StageAdvancementError(
            "expected_initial_stage is required "
            "when current_stage is None"
        )

    # ── Guard 4: initial stage contract ──────────────────────────────────
    if expected_initial_stage is not None:
        if pipeline.current_stage is None:
            if stage.name != expected_initial_stage:
                raise MissingInitialStageError(
                    f"Cannot start pipeline: expected initial stage "
                    f"'{expected_initial_stage}', got '{stage.name}'"
                )
        elif pipeline.current_stage == expected_initial_stage:
            if stage.name != expected_initial_stage:
                raise InitialStageAdvancementError(
                    f"Cannot advance from initial stage "
                    f"'{expected_initial_stage}' to '{stage.name}'"
                )

    # ── Guard 5: stage must not be already completed ─────────────────────
    if stage.status == "completed":
        raise StageAlreadyTerminalError(
            f"Cannot advance stage '{stage.name}' — "
            f"stage is already completed"
        )

    # ── Guard 6: stage must not be already failed ────────────────────────
    if stage.status == "failed":
        raise StageAlreadyTerminalError(
            f"Cannot advance stage '{stage.name}' — "
            f"stage is already failed"
        )

    # ── Guard 7: stage must be in STAGE_ORDER ────────────────────────────
    if stage.name not in STAGE_ORDER:
        raise StageNotInOrderError(
            f"Cannot advance stage '{stage.name}' — "
            f"'{stage.name}' is not in STAGE_ORDER"
        )


def advance_pipeline(pipeline: Pipeline) -> None:
    """Advance a running pipeline to its next stage or mark it completed."""
    _write_orchestrator_log(
        pipeline,
        "DEBUG",
        f"advance_pipeline called: status={pipeline.status}, "
        f"current_stage={pipeline.current_stage}",
    )

    # The orchestrator loop can continue executing a stage that was
    # already mid-flight when an abort request marks the pipeline as
    # cancelled. Do not attempt any further stage transitions once the
    # pipeline is no longer actively running.
    if pipeline.status != "running":
        _write_orchestrator_log(
            pipeline,
            "WARN",
            f"Pipeline no longer running (status={pipeline.status}) — "
            f"advance_pipeline call discarded",
        )
        return

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

    try:
        stage = pipeline.stages.get(name=next_stage_name)
    except PipelineStage.DoesNotExist:
        _write_orchestrator_log(
            pipeline,
            "CRITICAL",
            f"Pipeline stage row missing for expected stage "
            f"'{next_stage_name}'",
        )
        _transition_pipeline_state(
            pipeline,
            "failed",
            error_message=(
                "Pipeline stage row missing for expected stage "
                f"'{next_stage_name}'"
            ),
        )
        _teardown_workspace(pipeline)
        return
    try:
        # Only enforce the initial-stage contract on the very first
        # advance (when current_stage is None).  Normal stage-to-stage
        # progression (init→RED→GREEN…) uses basic validation only.
        _validate_stage_advancement(
            pipeline, stage,
            expected_initial_stage=(
                STAGE_ORDER[0] if not current_stage else None
            ),
        )
    except StageAdvancementError as exc:
        _write_orchestrator_log(
            pipeline,
            "WARN",
            f"Stage advancement validation failed: {exc}",
        )
        return
    if stage.retry_after and stage.retry_after > dj_timezone.now():
        # The agent may have written a terminal status between ticks.
        # Re-check state.json before blocking on the retry guard.
        if not _try_recover_retry(pipeline, stage):
            return
        # Recovery succeeded — next tick will advance to the following stage.
        _write_orchestrator_log(
            pipeline,
            "INFO",
            f"Recovered stage {stage.name} from retry via state.json "
            f"(status={stage.status})",
        )
        return

    _run_stage(pipeline, stage)

    # ── Init-to-RED transition on retry ─────────────────────────────────
    # _execute_pipeline bridges init→RED once on first pipeline start.
    # When init fails and subsequently succeeds on retry, _execute_pipeline
    # is never called again — its bridge was skipped because
    # _handle_stage_failure rolled current_stage back to None.
    # This bridge fires when init succeeds on a retry (retry_count > 0),
    # ensuring the pipeline does not get permanently stuck at init.
    if (stage.name == STAGE_ORDER[0]
            and stage.retry_count > 0
            and pipeline.current_stage == STAGE_ORDER[0]):
        try:
            next_stage = pipeline.stages.get(name=STAGE_ORDER[1])
        except PipelineStage.DoesNotExist:
            _write_orchestrator_log(
                pipeline,
                "CRITICAL",
                f"Stage row '{STAGE_ORDER[1]}' missing after init retry",
            )
            _transition_pipeline_state(
                pipeline,
                "failed",
                error_message=(
                    f"Stage row '{STAGE_ORDER[1]}' missing after init retry"
                ),
            )
            _teardown_workspace(pipeline)
            return
        _run_stage(pipeline, next_stage)


def _run_stage(pipeline: Pipeline, stage: PipelineStage) -> None:
    """Execute a single pipeline stage by spawning an agent container."""
    try:
        # Safety-net validation — only checks basic invariants that
        # could change between advance_pipeline's validation and this
        # point (e.g. a concurrent abort).  The initial-stage contract
        # is enforced in advance_pipeline, not here.
        if pipeline.status != "running":
            raise PipelineNotRunningError(
                "Cannot advance stage on pipeline "
                f"with status '{pipeline.status}' — must be 'running'"
            )
        if stage is None:
            raise StageNotFoundError(
                "cannot advance None stage — no stage rows exist yet"
            )
        if stage.status == "completed":
            raise StageAlreadyTerminalError(
                f"Cannot advance stage '{stage.name}' — "
                f"stage is already completed"
            )
        if stage.status == "failed":
            raise StageAlreadyTerminalError(
                f"Cannot advance stage '{stage.name}' — "
                f"stage is already failed"
            )
        if stage.name not in STAGE_ORDER:
            raise StageNotInOrderError(
                f"Cannot advance stage '{stage.name}' — "
                f"'{stage.name}' is not in STAGE_ORDER"
            )
    except StageAdvancementError as exc:
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Illegal stage advancement in _run_stage: {exc}",
        )
        _handle_stage_failure(pipeline, stage)
        return

    pipeline.current_stage = stage.name
    pipeline.save(update_fields=["current_stage", "updated_at"])

    stage.status = "running"
    stage.started_at = dj_timezone.now()
    stage.retry_after = None
    stage.save(update_fields=["status", "started_at", "retry_after"])

    # On retry, reset the stage entry in state.json so that the polling
    # loop in _run_stage_via_server does not immediately re-read a stale
    # terminal status (e.g. "failed") left by the previous attempt.
    # The agent will overwrite this with "completed"/"blocked" when it
    # finishes.  First-run stages keep whatever status is in state.json
    # (typically "pending" from _init_state_file).
    if stage.retry_count > 0:
        _write_stage_field(pipeline, stage.name, "status", "running")

    _write_orchestrator_log(
        pipeline,
        "INFO",
        f"Stage {stage.name} starting (retry {stage.retry_count})",
    )

    state_file = _state_file_path(pipeline)
    _write_state_field(state_file, "current_stage", stage.name)

    # The init stage is responsible for pipeline setup (workspace, server).
    # Only run setup if the workspace has not already been created (e.g. by
    # _execute_pipeline).
    if stage.name == "init" and not _state_file_path(pipeline).exists():
        try:
            _create_workspace(pipeline)
            _start_opencode_server(pipeline)
            _wait_for_server_health(pipeline)
        except Exception as exc:
            stage.status = "failed"
            stage.save(update_fields=["status"])
            _transition_pipeline_state(pipeline, "failed")
            _write_orchestrator_log(
                pipeline,
                "CRITICAL",
                f"Failed to create workspace during init stage: {exc}",
            )
            _teardown_workspace(pipeline)
            return

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
            # The init stage is a setup barrier — after its agent
            # completes, keep it as "pending" so the advancement
            # guard in advance_pipeline (line 466-469) blocks any
            # attempt to advance FROM init.  The transition from
            # init to the next stage is handled by _execute_pipeline
            # directly, not through the normal chain.
            if stage.name == STAGE_ORDER[0]:
                stage.status = "pending"
            else:
                stage.status = "completed"
            stage.save(update_fields=["status", "finished_at"])
            _write_orchestrator_log(
                pipeline,
                "INFO",
                f"Stage {stage.name} completed in {duration:.1f}s",
            )
            if stage.name == "GREEN":
                _run_formatters(pipeline)
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


def _transition_pipeline_state(
    pipeline: Pipeline,
    target_status: str,
    *,
    revive: bool = False,
    error_message: str = "",
) -> None:
    """Edit the pipeline status in the database.

    Only the orchestrator thread should call this.  Refreshes the pipeline
    from the database to detect cross-thread status changes, then refuses
    to transition a terminal pipeline to a non-terminal status unless
    ``revive=True`` is explicitly passed.

    When *error_message* is non-empty, it is written to the pipeline's
    ``error_message`` field alongside the status update.  This replaces
    the previous pattern of direct ``pipeline.status`` / ``error_message``
    assignment (see the ``DoesNotExist`` handler in ``advance_pipeline``).

    Raises:
        RuntimeError: If the transition would revive a terminal pipeline
                      without ``revive=True``.
    """
    pipeline.refresh_from_db(fields=["status"])
    if (
        pipeline.status in TERMINAL_STATUSES
        and target_status in NON_TERMINAL_STATUSES
        and not revive
    ):
        raise RuntimeError(
            f"Cannot revive pipeline {pipeline.id}: "
            f"current status={pipeline.status}, "
            f"target={target_status}"
        )
    pipeline.status = target_status
    if error_message:
        pipeline.error_message = error_message
        pipeline.save(update_fields=["status", "error_message", "updated_at"])
    else:
        pipeline.save(update_fields=["status", "updated_at"])


def _handle_stage_failure(pipeline: Pipeline, stage: PipelineStage) -> None:
    """Handle a failed stage with retry logic (non-blocking)."""
    stage.retry_count += 1

    # ── Guard 1: pending abort signal ──────────────────────────────────
    # Check the cross-process FIFO source (_pending_aborts).
    if str(pipeline.id) in _pending_aborts:
        _pending_aborts.discard(str(pipeline.id))
        stage.status = "failed"
        with transaction.atomic():
            stage.save(update_fields=["status", "retry_count"])
        _transition_pipeline_state(pipeline, "cancelled")
        _write_orchestrator_log(pipeline, "INFO", "Pipeline aborted by user")
        _teardown_workspace(pipeline)
        return

    # ── Guard 2: pipeline already dead in the database ─────────────────
    # Handles the cross-thread race where a concurrent WSGI thread has
    # already set pipeline.status to a terminal value (e.g. "cancelled"
    # from abort_pipeline in the old design, or a direct DB write).
    pipeline.refresh_from_db(fields=["status"])
    if pipeline.status in TERMINAL_STATUSES:
        stage.status = "failed"
        with transaction.atomic():
            stage.save(update_fields=["status", "retry_count"])
        return

    if stage.retry_count > PIPELINE_MAX_RETRIES:
        stage.status = "failed"
        with transaction.atomic():
            stage.save(update_fields=["status", "retry_count"])
        _transition_pipeline_state(pipeline, "failed")
        _write_orchestrator_log(
            pipeline,
            "ERROR",
            f"Stage {stage.name} failed after {stage.retry_count} retries",
        )
        _teardown_workspace(pipeline)
        return

    backoff_idx = min(
        stage.retry_count - 1,
        len(settings.PIPELINE_RETRY_BACKOFF_SECONDS) - 1,
    )
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
        f"Stage {stage.name} retry {stage.retry_count}/{PIPELINE_MAX_RETRIES} in {delay}s",
    )


def _complete_pipeline(pipeline: Pipeline) -> None:
    """Mark a pipeline as completed and attempt PR creation."""
    _transition_pipeline_state(pipeline, "completed")
    _write_orchestrator_log(pipeline, "INFO", "Pipeline completed")
    try:
        _create_pr(pipeline)
    except Exception:
        logger.exception("PR creation failed for pipeline %s", pipeline.id)
    _stop_opencode_server(pipeline)
    _teardown_workspace(pipeline)


def _teardown_workspace(pipeline: Pipeline) -> None:
    """Remove workspace directory after pipeline completion or cancellation.

    Idempotent — once teardown has been initiated for a pipeline, subsequent
    calls exit immediately without logging or any further side effects.

    Logs survive independently at /var/log/Wywy-Website/agentic/{pipeline_id}/.
    """
    key = str(pipeline.id)
    if key in _teardown_completed:
        return
    _teardown_completed.add(key)
    _stop_opencode_server(pipeline)
    workspace_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
    if workspace_dir.exists():
        _write_orchestrator_log(pipeline, "INFO", "Tearing down workspace")
        shutil.rmtree(str(workspace_dir), ignore_errors=True)
    _write_orchestrator_log(
        pipeline, "INFO",
        f"Pipeline ended (status={pipeline.status})",
    )


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


def _copy_source_tree(source: str, dest: str) -> None:
    """Copy source directory to dest, skipping the known-inaccessible secrets/ directory.

    shutil.copytree's ``ignore`` callback is invoked **before** the walk
    descends into a subdirectory, so ``secrets/`` can be filtered out before
    a PermissionError would be raised trying to list it.
    """
    def _ignore_fn(dir_path: str, contents: list[str]) -> list[str]:
        return ["secrets"] if "secrets" in contents else []

    shutil.copytree(
        source, dest,
        symlinks=True,
        dirs_exist_ok=False,
        ignore=_ignore_fn,
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

    from apps.orchestrator.config_writer import write_pipeline_config

    write_pipeline_config(workspace, pipeline)

    for source in COPY_SOURCES:
        rel_path = os.path.relpath(source, _COPY_SOURCES_BASE)
        dest = copies_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_orchestrator_log(pipeline, "INFO", f"Copying {source}...")
        _copy_source_tree(source, str(dest))

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


def _write_stage_field(
    pipeline: Pipeline, stage_name: str, key: str, value: object,
) -> None:
    """Atomically set a field in a stage entry inside ``state.json``.

    ``_write_state_field`` writes at the top level (``state[key]``); this
    helper writes into the nested ``state["stages"][stage_name][key]``.
    Used to mirror the orchestrator's view of stage state into the shared
    file so that ``_run_stage_via_server``'s polling loop sees the correct
    status rather than a stale value left by a previous run.
    """
    state_path = _state_file_path(pipeline)
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    stages = state.setdefault("stages", {})
    stages.setdefault(stage_name, {})[key] = value
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
    """Execute a stage via the opencode HTTP server.

    Delegates to _run_stage_via_server for the actual HTTP communication.
    Kept as a separate function so existing test mocks on
    _spawn_agent_container continue to work.
    """
    return _run_stage_via_server(pipeline, stage)




def _check_blocked_state(pipeline: Pipeline, stage: PipelineStage) -> bool:
    """Check state.json to determine if the stage is blocked on user input."""
    state = _read_state_field(pipeline, "stages")
    if isinstance(state, dict):
        stage_state = state.get(stage.name)
        if isinstance(stage_state, dict):
            if stage_state.get("status") == "blocked":
                return True
    return False


# ── OpenCode server pipeline ─────────────────────────────────────────────

def _server_container_name(pipeline_id: str) -> str:
    return f"pipeline-{pipeline_id}"


def _get_server_url(pipeline: Pipeline) -> str:
    container = _opencode_server_containers.get(str(pipeline.id))
    if container is None:
        raise RuntimeError(f"No opencode server for pipeline {pipeline.id}")
    container.reload()
    networks = container.attrs["NetworkSettings"]["Networks"]
    ip = networks[settings.AGENT_NETWORK]["IPAddress"]
    return f"http://{ip}:{settings.OPENCODE_SERVER_PORT}"


def _opencode_post(pipeline: Pipeline, path: str, json: dict | None = None,
                   timeout: int | None = None) -> dict:
    base = _get_server_url(pipeline)
    url = f"{base}{path}"
    auth = None
    if settings.OPENCODE_SERVER_PASSWORD:
        auth = (settings.OPENCODE_SERVER_USERNAME, settings.OPENCODE_SERVER_PASSWORD)
    resp = requests.post(url, json=json, auth=auth,
                         timeout=timeout or settings.PIPELINE_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _opencode_get(pipeline: Pipeline, path: str, timeout: int = 10) -> dict:
    base = _get_server_url(pipeline)
    url = f"{base}{path}"
    auth = None
    if settings.OPENCODE_SERVER_PASSWORD:
        auth = (settings.OPENCODE_SERVER_USERNAME, settings.OPENCODE_SERVER_PASSWORD)
    resp = requests.get(url, auth=auth, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _start_opencode_server(pipeline: Pipeline) -> None:
    """Start a persistent opencode serve container for the pipeline."""
    key = str(pipeline.id)
    if key in _opencode_server_containers:
        raise RuntimeError(f"Pipeline {pipeline.id} already has a server container")
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
        str(log_dir): {"bind": "/logs", "mode": "rw"},
    })

    client = docker.from_env()
    container = client.containers.run(
        image=settings.AGENT_IMAGE,
        command=[
            "opencode", "serve",
            "--port", str(settings.OPENCODE_SERVER_PORT),
            "--hostname", settings.OPENCODE_SERVER_HOSTNAME,
        ],
        environment={
            "PIPELINE_ID": str(pipeline.id),
            "HOME": "/home/wywy",
            "OPENCODE_SERVER_PASSWORD": settings.OPENCODE_SERVER_PASSWORD,
            "OPENCODE_API_KEY": getattr(settings, "AGENT_OPENCODE_API_KEY", ""),
            "DEEPSEEK_API_KEY": getattr(settings, "AGENT_DEEPSEEK_API_KEY", ""),
            "OPENAI_API_KEY": getattr(settings, "AGENT_OPENAI_API_KEY", ""),
            "ANTHROPIC_API_KEY": getattr(settings, "AGENT_ANTHROPIC_API_KEY", ""),
        },
        volumes=volumes,
        name=_server_container_name(str(pipeline.id)),
        user=f":{settings.AGENT_CONTAINER_GID}",
        detach=True,
        network=settings.AGENT_NETWORK,
    )
    _opencode_server_containers[key] = container
    _write_orchestrator_log(pipeline, "INFO", "Opencode server container started")


def _stop_opencode_server(pipeline: Pipeline) -> None:
    """Stop and remove the opencode serve container for the pipeline."""
    key = str(pipeline.id)
    container = _opencode_server_containers.pop(key, None)
    if container is None:
        return
    _capture_server_logs(pipeline, container)
    try:
        container.remove(force=True)
    except docker.errors.DockerException:
        logger.warning("Failed to remove server container for pipeline %s", pipeline.id)
    _write_orchestrator_log(pipeline, "INFO", "Opencode server container stopped")


def _wait_for_server_health(pipeline: Pipeline) -> None:
    """Poll /global/health until the server responds 200."""
    for i in range(settings.OPENCODE_SERVER_HEALTH_RETRIES):
        try:
            _opencode_get(pipeline, "/global/health", timeout=5)
            _write_orchestrator_log(pipeline, "INFO", "Opencode server healthy")
            return
        except Exception:
            if i < settings.OPENCODE_SERVER_HEALTH_RETRIES - 1:
                time.sleep(settings.OPENCODE_SERVER_HEALTH_INTERVAL)
    raise RuntimeError(
        f"Opencode server for pipeline {pipeline.id} failed health check "
        f"after {settings.OPENCODE_SERVER_HEALTH_RETRIES} retries"
    )


def _write_log_file(pipeline: Pipeline, filename: str, content: str) -> None:
    """Append content to a log file in the pipeline log directory."""
    log_dir = Path(settings.LOG_ROOT) / str(pipeline.id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / filename
    try:
        with open(log_path, "a") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
    except Exception:
        logger.warning("Failed to write log %s for pipeline %s", filename, pipeline.id)


def _capture_server_logs(pipeline: Pipeline, container: Container) -> None:
    """Capture server container stdout/stderr to the pipeline log directory."""
    try:
        raw = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Failed to retrieve server logs for pipeline %s", pipeline.id)
        return
    _write_log_file(pipeline, "server.log", raw)


def _run_stage_via_server(pipeline: Pipeline, stage: PipelineStage) -> tuple[int, bool]:
    """Execute a pipeline stage via the opencode HTTP server."""
    # Create session
    session = _opencode_post(pipeline, "/session", json={"title": stage.name})
    session_id = session["id"]

    # Send prompt and wait for response
    _opencode_post(
        pipeline,
        f"/session/{session_id}/message",
        json={
            "parts": [
                {
                    "type": "text",
                    "text": (
                        f"Stage: {stage.name}. "
                        f"Write to /state/state.json to report your progress. "
                        f"Set stages.{stage.name}.status to 'completed' when done."
                    ),
                }
            ]
        },
    )

    # ── Poll state.json until the agent writes a terminal status ────────
    state_path = _state_file_path(pipeline)
    poll_deadline = time.time() + STATE_POLL_TIMEOUT
    is_blocked = False
    exit_code = 0
    while time.time() < poll_deadline:
        try:
            state = json.loads(state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(STATE_POLL_INTERVAL)
            continue
        stage_state = state.get("stages", {}).get(stage.name, {})
        status = stage_state.get("status")
        if status in ("completed", "blocked", "failed"):
            is_blocked = status == "blocked"
            if is_blocked:
                _set_blocked_input(pipeline, stage_state)
            exit_code = 1 if status == "failed" else 0
            break
        if _check_blocked_state(pipeline, stage):
            is_blocked = True
            break
        time.sleep(STATE_POLL_INTERVAL)

    # Capture session messages as stage log
    try:
        messages = _opencode_get(pipeline, f"/session/{session_id}/message")
        _write_log_file(pipeline, f"{stage.name}.log", json.dumps(messages, indent=2))
    except Exception:
        logger.warning("Failed to capture session messages for stage %s", stage.name)

    return exit_code, is_blocked


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
    pr_writer_stage = pipeline.stages.filter(name="PR writer").first()
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


def _read_api_key(key_name: str, secrets_dir: str | None = None) -> str:
    """Read an API key from a Docker-style secret file.

    The file name is derived from *key_name* by lowercasing and replacing
    underscores with hyphens (e.g. ``"OPENCODE_API_KEY"`` →
    ``"opencode-api-key"``).  The file is read from *secrets_dir* if given,
    otherwise from ``settings.API_KEY_SECRETS_DIR`` (default
    ``/run/secrets``).

    Returns the trimmed file content, or an empty string if the file does
    not exist or cannot be read (matching the previous fallback of
    ``getattr(settings, …, "")``).
    """
    if secrets_dir is None:
        secrets_dir = settings.API_KEY_SECRETS_DIR
    filename = key_name.lower().replace("_", "-")
    filepath = Path(secrets_dir) / filename
    try:
        return filepath.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return ""


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



def abort_pipeline(pipeline: Pipeline) -> None:
    """Request cancellation of a pipeline.

    Adds the pipeline ID to ``_pending_aborts`` for immediate same-process
    visibility (the orchestrator loop and ``_handle_stage_failure`` check
    this set), writes an abort signal to the cross-process FIFO for other
    workers, stops the agent container to unblock any in-flight HTTP call,
    and wakes the orchestrator loop.

    The orchestrator thread owns all DB and filesystem state mutations:
    it will consume the signal from ``_pending_aborts``, transition the
    pipeline status to ``"cancelled"`` via ``_transition_pipeline_state``,
    and tear down the workspace.
    """
    _pending_aborts.add(str(pipeline.id))
    try:
        fd = os.open(_SIGNAL_FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, f"abort:{pipeline.id}\n".encode())
        os.close(fd)
    except OSError:
        pass
    _stop_opencode_server(pipeline)
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
