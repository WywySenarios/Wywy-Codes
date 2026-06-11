"""Production Django settings for the orchestrator."""

import re
from config.settings.base import *  # noqa: F403, F401

DEBUG = False

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = False  # TLS terminated at reverse proxy

WHITENOISE_IMMUTABLE_FILE_TEST = lambda path: bool(re.search(r'/_\w+/', path))
