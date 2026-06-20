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

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from apps.orchestrator.models import Pipeline
from apps.orchestrator.orchestrator import abort_pipeline, wake_orchestrator, write_user_input_response
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


@csrf_exempt
def api_respond(request: HttpRequest, pipeline_id: str) -> HttpResponse:
    """Provide user guidance to a blocked pipeline."""
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

    user_input_count = len(
        [
            f
            for f in (Path(settings.WORKSPACE_ROOT) / str(pipeline_id) / "context" / "user-input").glob(
                "response_*.md"
            )
            if f.is_file()
        ]
    )
    response_dir = Path(settings.WORKSPACE_ROOT) / str(pipeline_id) / "context" / "user-input"
    response_dir.mkdir(parents=True, exist_ok=True)
    (response_dir / f"response_{user_input_count + 1}.md").write_text(response_text)

    write_user_input_response(pipeline, response_text)
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
