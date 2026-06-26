"""Base Django settings for the orchestrator API.

CONVENTION-EXCEPTION: Settings split into base/dev/prod files.
The django.mdx convention specifies a single settings.py with env-var-driven
behaviour. This split is required because the orchestrator runs in Docker
with distinct dev/prod environments that mount different config files,
and the plan (00-orchestrator.md) explicitly mandates this structure.
"""

from os import environ
from pathlib import Path
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY: str = get_random_secret_key()

DEBUG: bool = environ.get("DJANGO_DEBUG", "false").lower() == "true"

_AGENTIC_API_HOST: str = environ.get("AGENTIC_API_HOST", "")
_AGENTIC_WEBSITE_HOST: str = environ.get("AGENTIC_WEBSITE_HOST", "")
_AGENTIC_WEBSITE_PORT: str = environ.get("AGENTIC_WEBSITE_PORT", "3000")
_AGENTIC_WEBSITE_DEV_PORT: str = environ.get("AGENTIC_WEBSITE_DEV_PORT", "3000")
_AGENTIC_API_DOCKER_HOST: str = environ.get("AGENTIC_API_DOCKER_HOST", "django")

ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", _AGENTIC_API_DOCKER_HOST]
if _AGENTIC_API_HOST:
    ALLOWED_HOSTS.append(_AGENTIC_API_HOST)

INSTALLED_APPS: list[str] = [
    "corsheaders",
    "rest_framework",
    "apps.orchestrator.apps.OrchestratorConfig",
]

MIDDLEWARE: list[str] = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

CORS_ALLOWED_ORIGINS: list[str] = [
    f"http://localhost:{_AGENTIC_WEBSITE_PORT}",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    f"http://127.0.0.1:{_AGENTIC_WEBSITE_PORT}",
    f"http://{_AGENTIC_API_DOCKER_HOST}:3000",
]
if _AGENTIC_WEBSITE_HOST:
    CORS_ALLOWED_ORIGINS.extend([
        f"https://{_AGENTIC_WEBSITE_HOST}",
        f"http://{_AGENTIC_WEBSITE_HOST}:{_AGENTIC_WEBSITE_PORT}",
        f"http://{_AGENTIC_WEBSITE_HOST}:{_AGENTIC_WEBSITE_DEV_PORT}",
        f"http://{_AGENTIC_WEBSITE_HOST}",
    ])

ROOT_URLCONF: str = "config.urls"

TEMPLATES: list[dict] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION: str = "config.wsgi.application"

DATABASE_PATH: str = environ.get(
    "DJANGO_DATABASE_PATH",
    "/var/lib/Wywy-Website/orchestrator/db.sqlite3",
)

DATABASES: dict = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATABASE_PATH,
    }
}

DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"

LOG_ROOT: str = environ.get(
    "LOG_ROOT",
    "/tmp/wywy-test-logs" if environ.get("ENVIRONMENT") == "test" else "/var/log/Wywy-Website/agentic",
)

# Ensure the log directory exists before the LOGGING config is processed.
# RotatingFileHandler requires the parent directory to exist at setup time.
Path(LOG_ROOT).mkdir(parents=True, exist_ok=True)

# ── Logging configuration ──────────────────────────────────────────────
# All orchestrator log entries flow through Python's stdlib logging.
# The ``orchestrator.pipeline`` logger writes per-pipeline JSON-lines
# files via ``PipelineFileHandler``, while the root logger handles
# everything else (Django errors, HTTP requests, etc.).

LOGGING: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": '{"ts": "%(asctime)s", "level": "%(levelname)s", "src": "orchestrator", "msg": "%(message)s"}',
            "datefmt": "%Y-%m-%dT%H:%M:%S.%f",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(Path(LOG_ROOT) / "django.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "json",
        },
        "pipeline_file": {
            "level": "DEBUG",
            "class": "apps.orchestrator.state.logging.PipelineFileHandler",
        },
    },
    "loggers": {
        # Dedicated logger for per-pipeline orchestrator log entries.
        # Writes to {LOG_ROOT}/{pipeline_id}/orchestrator.log.
        # Does NOT propagate to the root logger — these entries are
        # persisted to their own files and do not need duplication
        # in console or django.log.
        "orchestrator.pipeline": {
            "handlers": ["pipeline_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        # System-level orchestrator events that have no pipeline context
        # (startup, agent network, orphan reaping, etc.).
        # Writes to {LOG_ROOT}/orchestrator.log alongside per-pipeline dirs.
        "orchestrator": {
            "handlers": ["console", "orchestrator_file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
}
# Shared handler for system-level orchestrator events (no pipeline context).
LOGGING["handlers"]["orchestrator_file"] = {
    "class": "logging.handlers.RotatingFileHandler",
    "filename": str(Path(LOG_ROOT) / "orchestrator.log"),
    "maxBytes": 10 * 1024 * 1024,
    "backupCount": 3,
    "formatter": "json",
}

# Whitenoise / Astro static files
ASTRO_DIST: str = environ.get(
    "ASTRO_DIST",
    str(BASE_DIR.parent.parent / "apps" / "astro" / "dist"),
)
STATIC_URL: str = "/"
MEDIA_URL: str = "/media/"
STATICFILES_DIRS: list[str] = [ASTRO_DIST]
STATIC_ROOT: str = ASTRO_DIST
WHITENOISE_INDEX_FILE: bool = True

AGENT_IMAGE: str = environ.get("AGENT_IMAGE", "wywy/agent")
AGENT_CONTAINER_GID: int = int(environ.get("AGENT_CONTAINER_GID", "2523"))
PIPELINE_TIMEOUT_SECONDS: int = int(environ.get("PIPELINE_TIMEOUT_SECONDS", "600"))
PIPELINE_MAX_RETRIES: int = int(environ.get("PIPELINE_MAX_RETRIES", "3"))
PIPELINE_MAX_ITERATIONS: int = int(environ.get("PIPELINE_MAX_ITERATIONS", "5"))
PIPELINE_RETRY_BACKOFF_SECONDS: list[int] = [
    int(x) for x in environ.get("PIPELINE_RETRY_BACKOFF_SECONDS", "30,60,120").split(",")
]
WEB_CONCURRENCY: int = int(environ.get("WEB_CONCURRENCY", "2"))
GITHUB_TOKEN_FILE: str = environ.get("GITHUB_TOKEN_FILE", "/run/secrets/github-pat")
API_KEY_SECRETS_DIR: str = environ.get("API_KEY_SECRETS_DIR", "/run/secrets")
WORKSPACE_ROOT: str = environ.get("WORKSPACE_ROOT", "/var/workspace/Wywy-Website")
CONTROL_REPO_PATH: str = environ.get("CONTROL_REPO_PATH", "/etc/Wywy-Website-Control")
AGENT_NETWORK: str = environ.get("AGENT_NETWORK", "wywy-agent-net")
OPENCODE_SERVER_PORT: int = int(environ.get("OPENCODE_SERVER_PORT", "4096"))
OPENCODE_SERVER_HOSTNAME: str = environ.get("OPENCODE_SERVER_HOSTNAME", "0.0.0.0")
OPENCODE_SERVER_PASSWORD: str = environ.get("OPENCODE_SERVER_PASSWORD", "")
OPENCODE_SERVER_USERNAME: str = environ.get("OPENCODE_SERVER_USERNAME", "opencode")
OPENCODE_SERVER_HEALTH_RETRIES: int = int(environ.get("OPENCODE_SERVER_HEALTH_RETRIES", "30"))
OPENCODE_SERVER_HEALTH_INTERVAL: float = float(environ.get("OPENCODE_SERVER_HEALTH_INTERVAL", "2.0"))
OPENCODE_DEFAULT_MODEL: str = environ.get("OPENCODE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5")
OPENCODE_SMALL_MODEL: str = environ.get("OPENCODE_SMALL_MODEL", "anthropic/claude-haiku-4-5")
OPENCODE_WARMUP: bool = environ.get("OPENCODE_WARMUP", "1") == "1"
STAGE_MODEL_MAP: dict[str, dict[str, str]] = {
    "init":       {"model": OPENCODE_DEFAULT_MODEL},
    "RED":        {"model": "deepseek/deepseek-chat"},
    "GREEN":      {"model": "deepseek/deepseek-chat"},
    "REFRACTOR":  {"model": "anthropic/claude-sonnet-4-5"},
    "compliance": {"model": "anthropic/claude-haiku-4-5"},
    "PR writer":  {"model": "anthropic/claude-sonnet-4-5"},
}
