"""Tests for Story 2: File lock orchestrator election.

These tests verify that ``OrchestratorConfig.ready()`` uses ``fcntl.flock``
to elect a single orchestrator worker among N Gunicorn workers.

Every test in this file is **RED** — it asserts lock-based behavior that
does not exist yet in the production code (``apps.py`` currently starts
the orchestrator thread unconditionally).  The tests will pass after
Story 2 (Green) implements:
  - ``_ORCHESTRATOR_LOCK_PATH`` constant in ``apps.py``
  - ``fcntl.flock`` acquisition in ``OrchestratorConfig.ready()``
  - Graceful skip when the lock is held by another worker
"""

from __future__ import annotations

import errno
import fcntl
import os
import threading
from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from apps.orchestrator.apps import OrchestratorConfig

# The lock path the orchestrator will use after Story 2 (Green).
LOCK_PATH = "/tmp/orchestrator.lock"

# Expected flags for the lock file open
LOCK_FILE_FLAGS = os.O_CREAT | os.O_RDWR


@pytest.fixture
def raw_config() -> OrchestratorConfig:
    """Return a fresh ``OrchestratorConfig`` instance for testing ``ready()``.

    Django's ``create()`` method expects a fully-registered app module, so
    we import the app module directly and pass it to the constructor.
    This bypasses the app registry so we can call ``ready()`` multiple
    times without side effects.
    """
    import apps.orchestrator.apps as apps_module
    return OrchestratorConfig(apps_module.__name__, apps_module)


class TestLockAcquired:
    """When ``fcntl.flock`` succeeds, the orchestrator thread must start."""

    def test_ready_calls_fcntl_flock_with_lock_ex_lock_nb(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """``OrchestratorConfig.ready()`` must call ``fcntl.flock`` with
        ``LOCK_EX | LOCK_NB`` when acquiring the orchestrator lock.

        Currently ``ready()`` does not use ``fcntl`` at all — it starts
        the orchestrator thread unconditionally.  This assertion will fail
        because ``fcntl.flock`` is never called.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Override the test env so the lock code path is exercised
        monkeypatch.setenv("ENVIRONMENT", "production")

        flock_calls: list[tuple[int, int]] = []

        def track_flock(fd: int, operation: int) -> None:
            flock_calls.append((fd, operation))

        monkeypatch.setattr(fcntl, "flock", track_flock)

        # Provide a fake fd for os.open
        monkeypatch.setattr(os, "open", lambda _p, _f, _m=0o644: 99)

        # Prevent thread.start from actually starting (no-op)
        monkeypatch.setattr(threading.Thread, "start", lambda _self: None)

        # ── Act ──────────────────────────────────────────────────────────
        raw_config.ready()

        # ── Assert ───────────────────────────────────────────────────────
        # Current code calls os.open only for the FIFO, not for a lock file.
        # This assertion will fail because fcntl.flock is never called.
        assert len(flock_calls) == 1, (
            f"Expected fcntl.flock to be called exactly once, "
            f"got {len(flock_calls)} "
            f"(ready() currently does not acquire a file lock)"
        )

        fd, operation = flock_calls[0]
        assert operation & fcntl.LOCK_EX, (
            "Lock must be exclusive (LOCK_EX)"
        )
        assert operation & fcntl.LOCK_NB, (
            "Lock must be non-blocking (LOCK_NB)"
        )

    def test_ready_opens_lock_file_with_o_creat_o_rdwr(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """The lock file must be created with ``O_CREAT | O_RDWR`` flags
        and mode ``0o644``.

        Currently ``ready()`` does not open any lock file, so this
        assertion will fail.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Override the test env so the lock code path is exercised
        monkeypatch.setenv("ENVIRONMENT", "production")

        open_calls: list[tuple[str, int, int]] = []

        def track_open(path: str, flags: int, mode: int = 0o644) -> int:
            open_calls.append((path, flags, mode))
            return 99

        monkeypatch.setattr(os, "open", track_open)
        monkeypatch.setattr(fcntl, "flock", lambda _fd, _op: None)
        monkeypatch.setattr(threading.Thread, "start", lambda _self: None)

        # ── Act ──────────────────────────────────────────────────────────
        raw_config.ready()

        # ── Assert ───────────────────────────────────────────────────────
        # Current code does not open a lock file.
        lock_opens = [
            oc for oc in open_calls if oc[0] == LOCK_PATH
        ]
        assert len(lock_opens) >= 1, (
            f"Expected at least one os.open for '{LOCK_PATH}', "
            f"got opens: {open_calls} "
            f"(ready() currently does not open a lock file)"
        )

        path, flags, mode = lock_opens[0]
        assert flags & os.O_CREAT, (
            f"Lock file must be opened with O_CREAT, got flags {flags}"
        )
        assert flags & os.O_RDWR, (
            f"Lock file must be opened with O_RDWR, got flags {flags}"
        )
        assert mode == 0o644, (
            f"Lock file must have mode 0o644, got {oct(mode)}"
        )


class TestLockContention:
    """When the lock is already held, the orchestrator thread must NOT start."""

    def test_ready_skips_thread_when_lock_held(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """If ``fcntl.flock`` raises ``IOError``/``OSError`` (lock already
        held by another worker), ``ready()`` must log and return without
        starting the orchestrator thread.

        Currently ``ready()`` never checks a lock — it always starts the
        thread.  This assertion will fail because the thread starts even
        when the simulated lock is held.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Override the test env so the lock code path is exercised
        monkeypatch.setenv("ENVIRONMENT", "production")

        # Simulate that another worker holds the lock
        def raise_lock_held(fd: int, operation: int) -> None:
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

        monkeypatch.setattr(fcntl, "flock", raise_lock_held)

        monkeypatch.setattr(os, "open", lambda _p, _f, _m=0o644: 99)

        # Track whether the orchestrator thread was started
        start_calls: list[str] = []

        def track_start(self: threading.Thread) -> None:
            start_calls.append(self.name)

        monkeypatch.setattr(threading.Thread, "start", track_start)

        # ── Act — must not raise ─────────────────────────────────────────
        try:
            raw_config.ready()
        except Exception as exc:
            pytest.fail(
                f"ready() must not raise when the lock is held, "
                f"got {type(exc).__name__}: {exc}"
            )

        # ── Assert ───────────────────────────────────────────────────────
        # Current code always starts the thread, so this will fail.
        orchestrator_starts = [
            name for name in start_calls if "orchestrator-loop" in name
        ]
        assert len(orchestrator_starts) == 0, (
            f"Orchestrator thread must NOT start when the lock is held. "
            f"Started threads: {start_calls} "
            f"(ready() currently ignores the lock and always starts the thread)"
        )

    def test_ready_handles_lock_error_gracefully(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """``ready()`` must catch ``OSError`` from ``fcntl.flock`` and
        return cleanly — the exception must not propagate to the caller.

        Currently the lock code does not exist, so this test will fail
        when the mock is triggered (or pass for the wrong reason if the
        mock never fires).
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Override the test env so the lock code path is exercised
        monkeypatch.setenv("ENVIRONMENT", "production")

        # Use a sentinel to detect whether the flock mock was actually called
        flock_was_called = False

        def raising_flock(fd: int, operation: int) -> None:
            nonlocal flock_was_called
            flock_was_called = True
            raise OSError(errno.EAGAIN, "Lock held by another worker")

        monkeypatch.setattr(fcntl, "flock", raising_flock)
        monkeypatch.setattr(os, "open", lambda _p, _f, _m=0o644: 99)
        monkeypatch.setattr(threading.Thread, "start", lambda _self: None)

        # ── Act — must not raise ─────────────────────────────────────────
        try:
            raw_config.ready()
        except Exception as exc:
            pytest.fail(
                f"ready() must catch lock errors gracefully, "
                f"got {type(exc).__name__}: {exc}"
            )

        # ── Assert ───────────────────────────────────────────────────────
        # If flock was never called, the test can't validate the graceful
        # handling.  This will fail because current code never calls flock.
        assert flock_was_called, (
            "fcntl.flock must be called to test error handling — "
            "current ready() does not use fcntl at all"
        )


class TestTestEnvironment:
    """``ENVIRONMENT=test`` must still skip the orchestrator thread entirely,
    regardless of lock state.
    """

    def test_test_env_skips_ready_even_if_lock_available(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """When ``ENVIRONMENT=test``, ``ready()`` must return early without
        acquiring a lock or starting a thread.

        This guard is the FIRST check in ``ready()`` and must be respected
        regardless of lock state.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setattr(fcntl, "flock", mock.MagicMock())
        monkeypatch.setattr(os, "open", lambda _p, _f, _m=0o644: 99)

        start_calls: list[str] = []
        monkeypatch.setattr(threading.Thread, "start", lambda self: start_calls.append(self.name))

        # ── Act ──────────────────────────────────────────────────────────
        raw_config.ready()

        # ── Assert ───────────────────────────────────────────────────────
        assert len(start_calls) == 0, (
            f"No thread should start when ENVIRONMENT=test, "
            f"got {start_calls}"
        )

    def test_test_env_skips_ready_even_if_lock_unavailable(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """When ``ENVIRONMENT=test``, ``ready()`` must return early even if
        the lock check would have succeeded.  Lock acquisition must not be
        attempted at all.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        monkeypatch.setenv("ENVIRONMENT", "test")

        # Track whether lock functions are called (they should NOT be)
        flock_calls: list[tuple[int, int]] = []
        monkeypatch.setattr(fcntl, "flock", lambda fd, op: flock_calls.append((fd, op)))

        open_calls: list[tuple[str, int, int]] = []
        monkeypatch.setattr(os, "open", lambda p, f, m=0o644: open_calls.append((p, f, m)) or 99)

        # ── Act ──────────────────────────────────────────────────────────
        raw_config.ready()

        # ── Assert ───────────────────────────────────────────────────────
        # Current code does not use these calls either (but doesn't use them
        # for the wrong reason — it just skips everything in test mode).
        # After Green, these must also be skipped.
        assert len(open_calls) == 0, (
            f"No os.open should be called when ENVIRONMENT=test "
            f"(lock file should not be created in test mode), got {open_calls}"
        )
        assert len(flock_calls) == 0, (
            f"No fcntl.flock should be called when ENVIRONMENT=test, "
            f"got {flock_calls}"
        )


class TestLockCleanup:
    """The lock file descriptor must be kept open for the process lifetime.
    On process exit, the kernel releases the lock and closes the FD.
    """

    def test_lock_fd_stored_on_config(
        self,
        monkeypatch: MonkeyPatch,
        raw_config: OrchestratorConfig,
    ) -> None:
        """The lock file descriptor returned by ``os.open`` must be stored
        (e.g. on the config instance or as a module global) so it stays
        open for the lifetime of the process.

        Currently there is no lock, so no fd is stored.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Override the test env so the lock code path is exercised
        monkeypatch.setenv("ENVIRONMENT", "production")

        monkeypatch.setattr(os, "open", lambda _p, _f, _m=0o644: 42)
        monkeypatch.setattr(fcntl, "flock", lambda _fd, _op: None)

        start_calls: list[str] = []
        monkeypatch.setattr(threading.Thread, "start", lambda self: start_calls.append(self.name))

        # ── Act ──────────────────────────────────────────────────────────
        raw_config.ready()

        # ── Assert ───────────────────────────────────────────────────────
        # The lock fd must be accessible to prevent garbage collection.
        # This assertion will fail because ready() does not store an fd.
        assert hasattr(raw_config, "_lock_fd") or hasattr(raw_config, "lock_fd"), (
            "Lock file descriptor must be stored on the config instance "
            "to keep it open for the process lifetime"
        )
        if hasattr(raw_config, "_lock_fd"):
            assert raw_config._lock_fd == 42
        elif hasattr(raw_config, "lock_fd"):
            assert raw_config.lock_fd == 42
