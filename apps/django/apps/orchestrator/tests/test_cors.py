"""Tests for CORS (Cross-Origin Resource Sharing) headers on API responses.

The Django server serves both the API and the Astro frontend (via Whitenoise).
Requests from known frontend origins must include Access-Control-Allow-Origin.
"""

from __future__ import annotations

# Origins that represent the frontend loading from different hosts/ports.
# These are the realistic origins the browser may send based on the env config:
#   AGENTIC_WEBSITE_HOST=lolipop
#   AGENTIC_WEBSITE_PORT=2525       (production)
#   AGENTIC_WEBSITE_DEV_PORT=3000   (development)
LOCALHOST_3000 = "http://localhost:3000"
LOCALHOST_2525 = "http://localhost:2525"
LOLIPOP_3000 = "http://lolipop:3000"
LOLIPOP_2525 = "http://lolipop:2525"


class TestCorsHeaders:
    """CORS headers must be present for all expected frontend origins."""

    URL = "/api/pipelines/"

    def test_localhost_3000_is_cors_allowed(self, client, db):
        """localhost:3000 is hardcoded in CORS_ALLOWED_ORIGINS."""
        response = client.get(self.URL, HTTP_ORIGIN=LOCALHOST_3000)
        assert response.status_code == 200
        assert response.has_header("Access-Control-Allow-Origin")
        assert response["Access-Control-Allow-Origin"] == LOCALHOST_3000

    def test_localhost_2525_is_cors_allowed(self, client, db):
        """A user accessing the production site at localhost:2525 should
        get CORS headers when the frontend calls the API."""
        response = client.get(self.URL, HTTP_ORIGIN=LOCALHOST_2525)
        assert response.status_code == 200
        assert response.has_header("Access-Control-Allow-Origin")
        assert response["Access-Control-Allow-Origin"] == LOCALHOST_2525

    def test_lolipop_2525_is_cors_allowed(self, client, db):
        """The production website origin must be CORS-allowed."""
        response = client.get(self.URL, HTTP_ORIGIN=LOLIPOP_2525)
        assert response.status_code == 200
        assert response.has_header("Access-Control-Allow-Origin")
        assert response["Access-Control-Allow-Origin"] == LOLIPOP_2525

    def test_lolipop_3000_is_cors_allowed(self, client, db):
        """The Astro dev server at lolipop:3000 must be CORS-allowed."""
        response = client.get(self.URL, HTTP_ORIGIN=LOLIPOP_3000)
        assert response.status_code == 200
        assert response.has_header("Access-Control-Allow-Origin")
        assert response["Access-Control-Allow-Origin"] == LOLIPOP_3000

    def test_preflight_options_returns_cors_headers(self, client, db):
        """OPTIONS preflight requests must return the proper CORS headers
        so the browser allows the actual request."""
        response = client.options(
            self.URL,
            HTTP_ORIGIN=LOCALHOST_3000,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        assert response.has_header("Access-Control-Allow-Origin")
        assert response["Access-Control-Allow-Origin"] == LOCALHOST_3000
