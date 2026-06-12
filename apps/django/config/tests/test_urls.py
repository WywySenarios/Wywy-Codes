"""Tests for config/urls.py — SPA fallback routing."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings


def _response_body(response: HttpResponse) -> str:
    """Get response body, handling both Whitenoise (streaming) and regular responses."""
    if hasattr(response, "streaming_content") and response.streaming_content:
        return b"".join(response.streaming_content).decode()
    return response.content.decode()


@pytest.fixture
def astro_dist() -> Generator[str]:
    """Create a temporary Astro dist directory with SPA shell files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dist = Path(tmpdir)

        # Root index.html (dashboard)
        (dist / "index.html").write_text("<html><body>Dashboard</body></html>")

        # _spa SPA shell for pipeline detail
        spa_dir = dist / "_spa"
        spa_dir.mkdir()
        (spa_dir / "index.html").write_text("<html><body>SPA Pipeline Detail</body></html>")

        # _spa/files SPA shell for file explorer
        files_dir = spa_dir / "files"
        files_dir.mkdir()
        (files_dir / "index.html").write_text("<html><body>SPA Files</body></html>")

        # _spa/respond SPA shell for response form
        respond_dir = spa_dir / "respond"
        respond_dir.mkdir()
        (respond_dir / "index.html").write_text("<html><body>SPA Respond</body></html>")

        yield tmpdir


UUID = "e1621a33-9b4d-40d8-bdd3-277fc72f2cdf"


class TestQueryStringSPARouting:
    """Test that _spa + ?id=<uuid> query string serves the SPA shell.

    This is the primary access pattern for pipeline dashboards.
    Since _spa is in getStaticPaths(), this works in both dev and prod.

    Note: _spa paths are served by Whitenoise (STATIC_ROOT), not _serve_astro.
    Whitenoise uses the real dist directory, so we only check status codes here.
    Content assertions are in TestUUIDPathBackwardCompat via _serve_astro.
    """

    def test_spa_with_id_query_string_returns_200(self) -> None:
        """GET /_spa/?id=<uuid> should return 200 (SPA shell served by Whitenoise)."""
        client = Client()
        response = client.get(f"/_spa/", {"id": UUID})
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_spa_files_with_id_query_string_returns_200(self) -> None:
        """GET /_spa/files/?id=<uuid> should return 200."""
        client = Client()
        response = client.get(f"/_spa/files/", {"id": UUID})
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_spa_respond_with_id_query_string_returns_200(self) -> None:
        """GET /_spa/respond/?id=<uuid> should return 200."""
        client = Client()
        response = client.get(f"/_spa/respond/", {"id": UUID})
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_spa_without_query_string_returns_200(self) -> None:
        """GET /_spa/ without query string should still serve (e.g., direct nav)."""
        client = Client()
        response = client.get("/_spa/")
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_spa_with_unknown_query_params_returns_200(self) -> None:
        """Query strings are passed through; the SPA shell is still served."""
        client = Client()
        response = client.get("/_spa/", {"extra": "value"})
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]


class TestUUIDPathBackwardCompat:
    """Verify that direct UUID-path URLs (/<uuid>/... ) still work.
    These are SPA-fallback URLs handled by _serve_astro in production.
    """

    def test_uuid_root_serves_spa_index(self, astro_dist: str) -> None:
        """GET /<uuid>/ should serve _spa/index.html (pipeline dashboard)."""
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get(f"/{UUID}/")
        assert response.status_code == 200
        assert "SPA Pipeline Detail" in _response_body(response)

    def test_uuid_files_serves_spa_files(self, astro_dist: str) -> None:
        """GET /<uuid>/files/ should serve _spa/files/index.html."""
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get(f"/{UUID}/files/")
        assert response.status_code == 200
        assert "SPA Files" in _response_body(response)

    def test_uuid_respond_serves_spa_respond(self, astro_dist: str) -> None:
        """GET /<uuid>/respond/ should serve _spa/respond/index.html."""
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get(f"/{UUID}/respond/")
        assert response.status_code == 200
        assert "SPA Respond" in _response_body(response)

    def test_uuid_without_trailing_slash_serves_spa(self, astro_dist: str) -> None:
        """GET /<uuid> (no trailing slash) should serve SPA shell."""
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get(f"/{UUID}")
        assert response.status_code == 200
        assert "SPA Pipeline Detail" in _response_body(response)


class TestStaticRoutes:
    """Test that static (non-dynamic) routes are served correctly."""

    def test_root_route_serves_dashboard(self, astro_dist: str) -> None:
        """GET / should serve the dashboard index.html (not SPA)."""
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get("/")
        assert response.status_code == 200
        assert "Dashboard" in _response_body(response)

    def test_missing_spa_dir_falls_back_to_root_index(self, astro_dist: str) -> None:
        """If _spa/ is missing, the root index.html should serve as fallback."""
        import shutil
        shutil.rmtree(Path(astro_dist) / "_spa")
        with override_settings(ASTRO_DIST=astro_dist):
            client = Client()
            response = client.get(f"/{UUID}/")
        assert response.status_code == 200
        assert "Dashboard" in _response_body(response)
