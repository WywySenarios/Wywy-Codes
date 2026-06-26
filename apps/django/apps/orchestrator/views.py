"""API views for the orchestrator. JSON-only responses — no templates.

CONVENTION-EXCEPTION: The django.mdx convention says "no Django REST Framework"
and "Raw SQL via psycopg". This project uses Django ORM directly because it
manages its own models (Pipeline, PipelineStage) with SQLite, and does not
interact with external databases. The plan (00-orchestrator.md) specifies
Django ORM models and DRF serializers explicitly.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import docker
from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from opencode_ai import APIStatusError, AsyncOpencode
from opencode_ai.types import (
    FilePart,
    Part,
    SnapshotPart,
    StepFinishPart,
    StepStartPart,
    TextPart,
    ToolPart,
)

from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.orchestrator import abort_pipeline, wake_orchestrator
from apps.orchestrator.serializers import pipeline_to_dict, stage_to_dict


# ── SDK type → log-entry mapping ─────────────────────────────────────


_PART_TYPE_MAP: dict[str, str] = {
    "text": "text",
    "tool": "tool_use",
    "step-start": "step_start",
    "step-finish": "step_finish",
    "file": "file",
    "snapshot": "snapshot",
}


def part_to_log_entry(part: dict | Part) -> dict:
    """Convert an opencode message part to a structured log entry.

    Accepts both legacy ``dict`` parts and SDK typed ``Part`` models.

    The returned dict has at least ``ts``, ``type``, and ``content`` keys
    so that session-derived entries have a uniform shape for the frontend.
    """
    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    if isinstance(part, dict):
        # ── Legacy dict-based part ─────────────────────────────────
        entry["type"] = part.get("type", "unknown")
        ptype = entry["type"]
        if ptype == "text":
            entry["content"] = part.get("text", "")
        elif ptype == "tool_use":
            entry["content"] = json.dumps(part.get("input", {}))
            if "name" in part:
                entry["name"] = part["name"]
        elif ptype == "tool_result":
            entry["content"] = part.get("content", part.get("text", ""))
        elif ptype == "input_required":
            entry["content"] = json.dumps(
                {k: v for k, v in part.items() if k != "type"}
            )
        else:
            entry["content"] = (
                part.get("text") or part.get("content") or json.dumps(part)
            )
        return entry

    # ── SDK typed Part model ───────────────────────────────────────
    entry["type"] = _PART_TYPE_MAP.get(part.type, part.type)

    if isinstance(part, TextPart):
        entry["content"] = part.text
    elif isinstance(part, ToolPart):
        state_input = getattr(part.state, "input", {})
        entry["content"] = json.dumps(state_input) if state_input else ""
        if part.tool:
            entry["name"] = part.tool
    elif isinstance(part, (StepStartPart, StepFinishPart)):
        entry["content"] = ""
    elif isinstance(part, FilePart):
        entry["content"] = part.url or ""
    elif isinstance(part, SnapshotPart):
        entry["content"] = part.snapshot or ""
    else:
        entry["content"] = ""
    return entry


def api_pipelines(request: HttpRequest) -> HttpResponse:
    """List pipelines (GET) or create a new pipeline (POST)."""
    if request.method == "GET":
        pipelines = Pipeline.objects.all()
        status_filter = request.GET.get("status", "")
        if status_filter:
            allowed = status_filter.split(",")
            pipelines = pipelines.filter(status__in=allowed)
        return JsonResponse({"pipelines": [pipeline_to_dict(p) for p in pipelines]})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponseBadRequest(
                json.dumps({"error": "Invalid JSON"}),
                content_type="application/json",
            )
        description = body.get("description", "")
        invocation_name = body.get("invocation_name", "")
        if not invocation_name:
            return HttpResponseBadRequest(
                json.dumps({"error": "invocation_name is required"}),
                content_type="application/json",
            )
        if not re.match(r"^[a-z0-9_-]+$", invocation_name):
            return HttpResponseBadRequest(
                json.dumps({"error": "invocation_name must contain only lowercase letters, digits, hyphens, and underscores"}),
                content_type="application/json",
            )
        pipeline = Pipeline.objects.create(
            invocation_name=invocation_name,
            description=description,
            status="queued",
        )
        wake_orchestrator()
        return JsonResponse(pipeline_to_dict(pipeline), status=201)

    return HttpResponseBadRequest(
        json.dumps({"error": "Method not allowed"}),
        content_type="application/json",
        status=405,
    )


def api_list_blocked_pipelines(request: HttpRequest) -> HttpResponse:
    """List all pipelines awaiting user input."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    pipelines = Pipeline.objects.filter(user_input_pending=True)
    return JsonResponse({"pipelines": [pipeline_to_dict(p) for p in pipelines]})


def api_pipeline_detail(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """Get pipeline detail with stages."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    pipeline = get_object_or_404(Pipeline, pk=pipeline_id)
    data = pipeline_to_dict(pipeline)
    data["stages"] = [stage_to_dict(s) for s in pipeline.stages.all()]
    return JsonResponse(data)


def api_pipeline_files(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """List files or serve a specific file from pipeline workspace."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    _ = get_object_or_404(Pipeline, pk=pipeline_id)

    file_path = request.GET.get("path", "")
    verbose = request.GET.get("verbose", "0") == "1"

    workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline_id)

    if file_path:
        full_path = (workspace / file_path).resolve()
        if not str(full_path).startswith(str(workspace.resolve())):
            return JsonResponse({"error": "Path traversal not allowed"}, status=400)
        if not full_path.exists() or not full_path.is_file():
            return JsonResponse({"error": "File not found"}, status=404)
        content = full_path.read_text()
        if full_path.suffix == ".json":
            return JsonResponse(json.loads(content), safe=False)
        return HttpResponse(content, content_type="text/plain; charset=utf-8")

    return JsonResponse(_list_pipeline_files(workspace, verbose))


def _list_pipeline_files(workspace: Path, verbose: bool) -> dict[str, list[dict[str, str | int]]]:
    files: dict[str, list[dict]] = {
        "artifacts": [],
        "summaries": [],
        "user_input": [],
        "logs": [],
        "other": [],
    }
    for fp in sorted(workspace.rglob("*")):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(workspace))
        entry = {"path": rel, "size": fp.stat().st_size}
        if rel.startswith("artifacts/"):
            files["artifacts"].append(entry)
        elif "summary_" in rel:
            if verbose or "verbose_summary_" not in rel:
                files["summaries"].append(entry)
        elif rel.startswith("context/user-input/"):
            files["user_input"].append(entry)
        elif rel.startswith("state/") or rel == "state.json":
            if verbose:
                files["other"].append(entry)
        elif rel.endswith(".log"):
            files["logs"].append(entry)
        elif verbose:
            files["other"].append(entry)
    return files


def _build_sdk_client(pipeline: Pipeline) -> AsyncOpencode:
    """Construct an ``AsyncOpencode`` client connected to the pipeline's container.

    Resolves the container's IP address on the agent network via the
    Docker SDK and returns a client configured for the opencode server.

    When *pipeline* has no ``container_id`` (e.g. in test or transitional
    state), falls back to the configured hostname — enough for the
    ``AsyncOpencode`` to be constructed; in production the container must
    exist for message delivery to succeed.
    """
    if pipeline.container_id:
        dkr = docker.from_env()
        container = dkr.containers.get(pipeline.container_id)
        ip = container.attrs["NetworkSettings"]["Networks"][
            settings.AGENT_NETWORK
        ]["IPAddress"]
        base_url = f"http://{ip}:{settings.OPENCODE_SERVER_PORT}"
    else:
        base_url = (
            f"http://{settings.OPENCODE_SERVER_HOSTNAME}:"
            f"{settings.OPENCODE_SERVER_PORT}"
        )
    password = settings.OPENCODE_SERVER_PASSWORD
    return AsyncOpencode(
        base_url=base_url,
        timeout=300.0,
        max_retries=2,
        default_headers={"Authorization": f"Bearer {password}"} if password else None,
    )


def _resolve_provider(model_id: str) -> str:
    """Extract the provider name from a model identifier.

    Returns the segment before ``/`` (e.g. ``"deepseek/deepseek-chat"`` → ``"deepseek"``).
    If the model has no ``/``, returns the model as-is.
    """
    return model_id.split("/")[0]


@csrf_exempt
def api_respond(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """Provide user guidance to a blocked pipeline.

    Looks up the blocked stage on the pipeline, sends the user's response
    as a follow-up message to the opencode session, clears the pending
    flag, and signals the orchestrator to resume.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    pipeline = get_object_or_404(Pipeline, pk=pipeline_id)
    if not pipeline.user_input_pending:
        return JsonResponse({"error": "Pipeline is not awaiting user input"}, status=400)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest(
            json.dumps({"error": "Invalid JSON"}),
            content_type="application/json",
        )

    selected_option = body.get("selected_option", "")
    freeform_response = body.get("freeform_response", "")

    response_text = freeform_response
    if selected_option:
        response_text = f"[Selected option: {selected_option}] {freeform_response}"

    # ── Find the blocked stage with a session_id ────────────────────────
    blocked_stage = pipeline.stages.filter(status="blocked").first()
    if blocked_stage is None or not blocked_stage.session_id:
        return JsonResponse(
            {"error": "Blocked stage has no session — cannot deliver response"},
            status=400,
        )

    # ── Send the user's response to the opencode session ────────────────
    client = _build_sdk_client(pipeline)
    try:
        async_to_sync(client.session.chat)(
            blocked_stage.session_id,
            model_id=settings.OPENCODE_DEFAULT_MODEL,
            provider_id=_resolve_provider(settings.OPENCODE_DEFAULT_MODEL),
            parts=[{"type": "text", "text": response_text}],
        )
    except APIStatusError:
        return JsonResponse(
            {"error": "Failed to deliver response to agent session"},
            status=502,
        )

    # ── Clear pending flag and wake orchestrator ────────────────────────
    pipeline.user_input_pending = False
    pipeline.save(update_fields=["user_input_pending", "updated_at"])

    wake_orchestrator()
    return JsonResponse({"status": "ok"})


@csrf_exempt
def api_abort(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """Abort a pipeline."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    pipeline = get_object_or_404(Pipeline, pk=pipeline_id)
    if pipeline.status in ("completed", "failed", "cancelled"):
        return JsonResponse(
            {"error": f"Pipeline is already {pipeline.status}"},
            status=400,
        )
    abort_pipeline(pipeline)
    return JsonResponse({"status": "ok"})


def _parse_log_entries(content: str, max_lines: int) -> list[dict]:
    """Parse log entries from *content*, returning the last *max_lines* entries.

    Supports two formats:
    1. A single JSON array or object (pretty-printed stage logs).
    2. JSON Lines (one JSON object per line, e.g. orchestrator logs).

    Entries that cannot be parsed are silently skipped.
    """
    stripped = content.strip()

    # Try whole-file JSON parsing first — handles pretty-printed JSON arrays
    # that stage logs are written as (json.dumps(messages, indent=2)).
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed[-max_lines:]
        elif isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    # Fall back to JSON Lines (one JSON object per line).
    entries: list[dict] = []
    for line in stripped.split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries[-max_lines:]


# ═══════════════════════════════════════════════════════════════════════
#  Log-reading helpers (shared by multiple log endpoints below)
# ═══════════════════════════════════════════════════════════════════════


def _read_log_file_entries(filepath: Path, max_lines: int) -> list[dict]:
    """Read a single log file and return parsed entries.

    Delegates to ``_parse_log_entries`` for the actual parsing (JSON-lines
    and JSON-array formats).  Returns an empty list when the file does not
    exist or cannot be read.

    Args:
        filepath: Absolute path to the log file.
        max_lines: Maximum number of entries to return from this file.

    Returns:
        Parsed log entries (newest up to *max_lines*).
    """
    try:
        content = filepath.read_text()
    except (FileNotFoundError, OSError):
        return []
    return _parse_log_entries(content, max_lines)


def _pipeline_log_files(log_root: Path, pipeline_id: str) -> list[Path]:
    """Return sorted paths to all ``*.log`` files in the pipeline log directory.

    Excludes hidden files (starting with ``.``) to match the convention
    in ``api_log_files``.
    """
    pipeline_dir = log_root / str(pipeline_id)
    if not pipeline_dir.exists():
        return []
    return sorted(
        f for f in pipeline_dir.iterdir()
        if f.is_file() and f.name.endswith(".log") and not f.name.startswith(".")
    )


def _ts_key(entry: dict) -> str:
    """Sort key for log entries — sort by timestamp (empty ts sorts first)."""
    return entry.get("ts", "")


def _conversation_to_log_entries(entries: list[dict]) -> list[dict]:
    """Transform opencode session messages into LogEntry-compatible dicts.

    Opencode log files produced by ``_write_log_file`` contain a JSON array
    of message objects with ``info`` and ``parts`` keys.  The frontend's
    ``LogViewer`` expects ``ts``, ``level``, ``msg`` — this function bridges
    that gap.

    When *entries* do not look like an opencode conversation (the first entry
    lacks both ``info`` and ``parts`` keys), they are returned unchanged.
    """
    if (
        not entries
        or not isinstance(entries[0], dict)
        or "info" not in entries[0]
        or "parts" not in entries[0]
    ):
        return entries

    _ROLE_LEVELS = {"assistant": "AGENT", "user": "USER"}
    result: list[dict] = []
    for msg in entries:
        info = msg.get("info", {})
        role = info.get("role", "unknown")
        level = _ROLE_LEVELS.get(role, role.upper())

        # Convert milliseconds timestamp to ISO string
        created_millis = info.get("time", {}).get("created")
        if created_millis:
            ts = datetime.fromtimestamp(
                created_millis / 1000, tz=timezone.utc
            ).isoformat()
        else:
            ts = datetime.now(timezone.utc).isoformat()

        # Concatenate all parts into a single readable message
        parts_text: list[str] = []
        for part in msg.get("parts", []):
            text = part.get("text") or part.get("content") or ""
            if text:
                parts_text.append(text)
        msg_text = "\n".join(parts_text)

        result.append({
            "ts": ts,
            "level": level,
            "msg": msg_text,
        })

    return result


def api_log_files(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """List available log files for a pipeline."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    _ = get_object_or_404(Pipeline, pk=pipeline_id)
    log_root = Path(settings.LOG_ROOT)
    files = sorted(f.name for f in _pipeline_log_files(log_root, pipeline_id))
    return JsonResponse({"logs": files})


def api_log_entries(request: HttpRequest, pipeline_id: str, log_filename: str) -> HttpResponse:
    """Return JSON entries from a single log file, or raw text with ?raw."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    _ = get_object_or_404(Pipeline, pk=pipeline_id)
    log_dir = Path(settings.LOG_ROOT) / str(pipeline_id)

    log_file = (log_dir / log_filename).resolve()

    if "raw" in request.GET:
        if not log_file.exists():
            return JsonResponse({"error": "File not found"}, status=404)
        content = log_file.read_text()
        return HttpResponse(content, content_type="text/plain; charset=utf-8")

    # JSON entries mode
    if not log_file.exists():
        return JsonResponse({"entries": []})

    try:
        max_lines = int(request.GET.get("lines", "100"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "lines parameter must be an integer"}, status=400)

    content = log_file.read_text()
    entries = _parse_log_entries(content, max_lines)
    return JsonResponse({"entries": _conversation_to_log_entries(entries)})


# ── Stage-aware structured logs ──────────────────────────────────────── #


@csrf_exempt
def api_stage_logs(request: HttpRequest, pipeline_id: str, stage_name: str) -> HttpResponse:
    """Return structured log entries for a specific pipeline stage.

    When the stage has a ``session_id``, fetches typed message parts from
    the opencode server via ``AsyncOpencode.session.messages()`` and
    transforms them into log entries with ``type`` and ``content`` fields.

    Otherwise, falls back to reading ``{stage_name}.log`` from the
    filesystem.

    Always merges with ``orchestrator.log`` entries, sorted by timestamp.
    Supports ``?lines=N`` filtering (default 100).
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    pipeline = get_object_or_404(Pipeline, pk=pipeline_id)
    stage = pipeline.stages.filter(name=stage_name).first()
    if stage is None:
        return JsonResponse({"error": "Stage not found"}, status=404)

    try:
        max_lines = int(request.GET.get("lines", "100"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "lines parameter must be an integer"}, status=400)

    log_dir = Path(settings.LOG_ROOT) / str(pipeline_id)
    entries: list[dict] = []

    # ── 1. Source — session messages or file-based stage log ──────────
    if stage.session_id:
        client = _build_sdk_client(pipeline)
        try:
            items = async_to_sync(client.session.messages)(stage.session_id)
        except APIStatusError:
            items = []
        for item in items:
            for part in item.parts:
                entries.append(part_to_log_entry(part))
    else:
        stage_log = log_dir / f"{stage_name}.log"
        entries.extend(_read_log_file_entries(stage_log, max_lines))

    # ── 2. Merge orchestrator.log entries ─────────────────────────────
    orch_log = log_dir / "orchestrator.log"
    for entry in _read_log_file_entries(orch_log, max_lines):
        entry.setdefault("type", "orchestrator")
        entries.append(entry)

    # ── 3. Sort by timestamp (empty ts sorts first) ───────────────────
    entries.sort(key=_ts_key)

    # ── 4. Apply lines limit ──────────────────────────────────────────
    entries = entries[-max_lines:]

    return JsonResponse({"entries": entries})


# ═══════════════════════════════════════════════════════════════════════
#  Cycle 7: merged-all and system log endpoints
# ═══════════════════════════════════════════════════════════════════════


def api_logs_all(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """Return entries from ALL log files for a pipeline, merged and sorted.

    Reads every ``*.log`` file in ``{LOG_ROOT}/{pipeline_id}/`` as well as
    the shared system ``{LOG_ROOT}/orchestrator.log``, parses JSON-lines
    and JSON-array formats, and returns a single timestamp-sorted list.

    Supports ``?lines=N`` (default 100).
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    _ = get_object_or_404(Pipeline, pk=pipeline_id)

    try:
        max_lines = int(request.GET.get("lines", "100"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "lines parameter must be an integer"}, status=400)

    log_root = Path(settings.LOG_ROOT)
    entries: list[dict] = []

    # 1. All per-pipeline log files
    for log_file in _pipeline_log_files(log_root, pipeline_id):
        entries.extend(_read_log_file_entries(log_file, max_lines))

    # 2. Shared system orchestrator.log (at LOG_ROOT root, not per-pipeline)
    system_log = log_root / "orchestrator.log"
    entries.extend(_read_log_file_entries(system_log, max_lines))

    # 3. Sort by timestamp (entries without ts sort first)
    entries.sort(key=_ts_key)

    # 4. Apply global line limit
    entries = entries[-max_lines:]

    return JsonResponse({"entries": entries})


def _single_file_log_view(request: HttpRequest, filename: str) -> HttpResponse:
    """Shared helper for views that read a single log file at LOG_ROOT."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        max_lines = int(request.GET.get("lines", "100"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "lines parameter must be an integer"}, status=400)

    log_file = Path(settings.LOG_ROOT) / filename
    entries = _read_log_file_entries(log_file, max_lines)

    return JsonResponse({"entries": entries})


def api_system_logs(request: HttpRequest) -> HttpResponse:
    """Return entries from the shared system-level orchestrator log.

    Reads ``{LOG_ROOT}/orchestrator.log``, which is written by the
    ``orchestrator`` logger (startup, agent network, orphan reaping,
    etc.).

    Supports ``?lines=N`` (default 100).
    """
    return _single_file_log_view(request, "orchestrator.log")


def api_django_logs(request: HttpRequest) -> HttpResponse:
    """Return entries from the Django application log.

    Reads ``{LOG_ROOT}/django.log``, which is written by the root logger's
    ``RotatingFileHandler`` (Django-level errors, tracebacks, etc.).

    Supports ``?lines=N`` (default 100).
    """
    return _single_file_log_view(request, "django.log")


def api_logs_spa(request: HttpRequest) -> HttpResponse:
    """Return consolidated log data for the SPA in a single response.

    Always includes system orchestrator log and Django log entries.
    When ``?pipeline_id=`` is provided, also includes per-pipeline log
    files and merged entries.

    Query parameters:
        pipeline_id (str, optional): UUID of a pipeline to scope logs to.
        lines (int, optional): Max entries per log source (default 100).
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        max_lines = int(request.GET.get("lines", "100"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "lines parameter must be an integer"}, status=400)

    log_root = Path(settings.LOG_ROOT)

    # Always include system orchestrator log and django log entries
    system_entries = _read_log_file_entries(log_root / "orchestrator.log", max_lines)
    django_entries = _read_log_file_entries(log_root / "django.log", max_lines)

    result: dict = {
        "system": system_entries,
        "django": django_entries,
    }

    pipeline_id = request.GET.get("pipeline_id")
    if pipeline_id:
        _ = get_object_or_404(Pipeline, pk=pipeline_id)
        log_files = _pipeline_log_files(log_root, pipeline_id)
        merged_entries: list[dict] = []
        for log_file in log_files:
            merged_entries.extend(_read_log_file_entries(log_file, max_lines))
        merged_entries.sort(key=_ts_key)
        result["pipeline"] = {
            "files": sorted(f.name for f in log_files),
            "entries": merged_entries,
        }

    return JsonResponse(result)
