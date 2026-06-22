"""API views for the orchestrator. JSON-only responses — no templates.

CONVENTION-EXCEPTION: The django.mdx convention says "no Django REST Framework"
and "Raw SQL via psycopg". This project uses Django ORM directly because it
manages its own models (Pipeline, PipelineStage) with SQLite, and does not
interact with external databases. The plan (00-orchestrator.md) specifies
Django ORM models and DRF serializers explicitly.
"""

import json
import re
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

from apps.orchestrator.agent_client import AgentClient, AgentClientError, part_to_log_entry
from apps.orchestrator.models import Pipeline, PipelineStage
from apps.orchestrator.orchestrator import abort_pipeline, wake_orchestrator
from apps.orchestrator.serializers import pipeline_to_dict, stage_to_dict


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


def _build_agent_client(pipeline: Pipeline) -> AgentClient:
    """Construct an ``AgentClient`` connected to the pipeline's container.

    Resolves the container's IP address on the agent network via the
    Docker SDK and returns a client configured with the server password.

    When *pipeline* has no ``container_id`` (e.g. in test or transitional
    state), falls back to the configured hostname — enough for the
    ``AgentClient`` to be constructed; in production the container must
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
    return AgentClient(
        base_url=base_url,
        password=settings.OPENCODE_SERVER_PASSWORD,
    )


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
    agent = _build_agent_client(pipeline)
    try:
        async_to_sync(agent.send_message)(
            blocked_stage.session_id,
            parts=[{"type": "text", "text": response_text}],
        )
    except AgentClientError:
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


def api_log_files(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """List available log files for a pipeline."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    _ = get_object_or_404(Pipeline, pk=pipeline_id)
    log_dir = Path(settings.LOG_ROOT) / str(pipeline_id)
    files: list[str] = []
    if log_dir.exists():
        for f in sorted(log_dir.iterdir()):
            if f.is_file() and f.name.endswith(".log") and not f.name.startswith("."):
                files.append(f.name)
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
    return JsonResponse({"entries": _parse_log_entries(content, max_lines)})


# ── Stage-aware structured logs ──────────────────────────────────────── #


@csrf_exempt
def api_stage_logs(request: HttpRequest, pipeline_id: str, stage_name: str) -> HttpResponse:
    """Return structured log entries for a specific pipeline stage.

    When the stage has a ``session_id``, fetches typed message parts from
    the opencode server via ``AgentClient.get_session_messages()`` and
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
        agent = _build_agent_client(pipeline)
        try:
            messages = async_to_sync(agent.get_session_messages)(stage.session_id)
        except AgentClientError:
            messages = []
        for msg in messages:
            for part in msg.parts:
                entries.append(part_to_log_entry(part))
    else:
        stage_log_path = log_dir / f"{stage_name}.log"
        if log_dir.exists() and stage_log_path.exists():
            content = stage_log_path.read_text()
            entries.extend(_parse_log_entries(content, max_lines))

    # ── 2. Merge orchestrator.log entries ─────────────────────────────
    orch_log_path = log_dir / "orchestrator.log"
    if log_dir.exists() and orch_log_path.exists():
        content = orch_log_path.read_text()
        for entry in _parse_log_entries(content, max_lines):
            entry.setdefault("type", "orchestrator")
            entries.append(entry)

    # ── 3. Sort by timestamp (empty ts sorts first) ───────────────────
    entries.sort(key=lambda e: e.get("ts", ""))

    # ── 4. Apply lines limit ──────────────────────────────────────────
    entries = entries[-max_lines:]

    return JsonResponse({"entries": entries})
