"""App-level test configuration.

``pytest_configure`` at this level runs early in the pytest session —
before ``django_db_setup`` creates the test database — because the
``orchestrator`` app package is traversed during initial discovery.
"""

from __future__ import annotations

import os

import pytest
from django.conf import settings


def pytest_configure(config) -> None:  # noqa: ARG001
    """Configure asyncio-safe test database before db fixtures run.

    Django defaults to an in-memory shared-cache test DB for SQLite
    (``file:memorydb_*?mode=memory&cache=shared``).  In shared-cache mode
    a RESERVED lock held by one connection blocks ALL other connections —
    even SHARED (read) locks.  By forcing a file path we get standard
    SQLite locking where SHARED reads coexist with a RESERVED lock.
    """
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    settings.DATABASES["default"].setdefault("TEST", {})
    settings.DATABASES["default"]["TEST"].setdefault(
        "NAME", "/tmp/test_async_orchestrator.sqlite3",
    )
    opts = settings.DATABASES["default"].setdefault("OPTIONS", {})
    opts.setdefault("timeout", 20)
    opts.setdefault("init_command", "PRAGMA journal_mode=WAL")
