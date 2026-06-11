"""Root URL routing for the orchestrator."""

from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse
from django.urls import include, path, re_path

def _serve_astro(request):
    dist = Path(settings.ASTRO_DIST)
    req_path = request.path.lstrip("/").rstrip("/") or "index"

    # Try exact file matches first
    candidates = [
        dist / req_path,
        dist / f"{req_path}.html",
        dist / req_path / "index.html",
    ]
    for candidate in candidates:
        if candidate.is_file():
            content = candidate.read_bytes()
            ct = "text/html" if candidate.suffix == ".html" else None
            return HttpResponse(content, content_type=ct or "application/octet-stream")

    # SPA fallback for dynamic routes
    parts = [p for p in req_path.split("/") if p]
    if parts:
        static_dirs = {"inbox", "new", "_astro", "api"}
        if parts[0] not in static_dirs and not (dist / parts[0]).is_dir():
            spa_page = dist / "_spa"
            if len(parts) >= 2 and parts[-1] == "files":
                spa_page = spa_page / "files" / "index.html"
            elif len(parts) >= 2 and parts[-1] == "respond":
                spa_page = spa_page / "respond" / "index.html"
            else:
                spa_page = spa_page / "index.html"
            if spa_page.is_file():
                return HttpResponse(spa_page.read_bytes(), content_type="text/html")

    # Final fallback
    index = dist / "index.html"
    if index.is_file():
        return HttpResponse(index.read_bytes(), content_type="text/html")
    raise Http404

urlpatterns = [
    path("", include("apps.orchestrator.urls")),
    re_path(r"^(?!api/).*$", _serve_astro),
]
