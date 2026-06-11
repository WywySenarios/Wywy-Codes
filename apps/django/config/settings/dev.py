"""Development Django settings for the orchestrator."""

from config.settings.base import *  # noqa: F403, F401

DEBUG = True

ALLOWED_HOSTS = ["*"]

LOGGING["root"]["level"] = "INFO"  # noqa: F405
