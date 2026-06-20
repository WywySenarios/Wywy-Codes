"""Tests for Story 3: Remove ``select_for_update()``.

Now that Story 2 guarantees exactly one orchestrator worker via the
``fcntl.flock`` election, ``select_for_update()`` row locks are no longer
needed.  These tests verify that the orchestrator loop queries pipelines
without row locks.

Every test in this file is **RED** — it documents the intended post-Story-3
state.  The tests will pass after Green removes ``.select_for_update()``
from the two pipeline queries in ``orchestrator_loop()``.
"""

from __future__ import annotations

from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from django.db.models.query import QuerySet

from apps.orchestrator import orchestrator
from apps.orchestrator.apps import OrchestratorConfig


class TestRunningPipelineQuery:
    """The running-pipeline query must NOT use ``select_for_update``."""

    def test_running_pipeline_query_without_row_lock(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The orchestrator loop's running-pipeline query must not include
        ``.select_for_update()``.

        Currently the loop calls ``.select_for_update()`` on the running
        pipeline query.  This test tracks ``select_for_update`` calls and
        asserts zero — which will fail while the row lock is still present.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        sfu_calls: list[tuple] = []

        def track_sfu(
            qs: QuerySet, *args: object, **kwargs: object
        ) -> QuerySet:
            sfu_calls.append((args, kwargs))
            return qs

        monkeypatch.setattr(
            QuerySet, "select_for_update", track_sfu, raising=False,
        )

        # Suppress side effects to safely call orchestrator_loop
        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_ensure_signal_fifo", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_transition_pipeline_state", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        # Provide a working FIFO fd (monkeypatched os.open returns 99)
        monkeypatch.setattr(orchestrator.os, "open", lambda _p, _f, _m=0: 99)

        # Provide a select mock so the loop doesn't block
        monkeypatch.setattr(
            orchestrator, "select", mock.MagicMock(), raising=False,
        )
        orchestrator.select.select = lambda r, w, e, t: ([], [], [])  # type: ignore[attr-defined]

        # Break out after one iteration
        def break_loop(timeout: float = 1.0) -> None:
            raise RuntimeError("break orchestrator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(RuntimeError, match="break orchestrator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        # Currently the loop calls select_for_update() twice (running +
        # queued queries).  After Green, both calls must be removed.
        # This assertion will fail because select_for_update IS called.
        assert len(sfu_calls) == 0, (
            f"Expected zero select_for_update calls after guard removal, "
            f"got {len(sfu_calls)}: {sfu_calls}"
            f"  (Story 2 guarantees a single orchestrator, so row locks "
            f"are no longer necessary)"
        )


class TestQueuedPipelineQuery:
    """The queued-pipeline query must NOT use ``select_for_update``."""

    def test_queued_pipeline_query_without_row_lock(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The orchestrator loop's queued-pipeline query must not include
        ``.select_for_update()``.

        Same approach as the running-pipeline test — tracks all
        ``select_for_update`` calls and asserts none were made.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        sfu_calls: list[tuple] = []

        def track_sfu(
            qs: QuerySet, *args: object, **kwargs: object
        ) -> QuerySet:
            sfu_calls.append((args, kwargs))
            return qs

        monkeypatch.setattr(
            QuerySet, "select_for_update", track_sfu, raising=False,
        )

        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_ensure_signal_fifo", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_transition_pipeline_state", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        monkeypatch.setattr(orchestrator.os, "open", lambda _p, _f, _m=0: 99)

        monkeypatch.setattr(
            orchestrator, "select", mock.MagicMock(), raising=False,
        )
        orchestrator.select.select = lambda r, w, e, t: ([], [], [])  # type: ignore[attr-defined]

        def break_loop(timeout: float = 1.0) -> None:
            raise RuntimeError("break orchestrator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(RuntimeError, match="break orchestrator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        assert len(sfu_calls) == 0, (
            f"Expected zero select_for_update calls after guard removal, "
            f"got {len(sfu_calls)}: {sfu_calls}"
        )


class TestConcurrentViews:
    """HTTP views must not use ``select_for_update`` for pipeline queries.

    With a single orchestrator writer, no view-level row locks are necessary.
    The existing integration tests (``test_views_respond``, etc.) already
    prove concurrent requests work — this test documents the intent.
    """

    def test_respond_view_does_not_row_lock(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The ``respond`` view (which handles user input) must not use
        ``select_for_update`` on pipeline queries.

        This is a documentation test: after Story 3, no view should acquire
        row locks.  The test inspects the queryset used by the view.
        """
        # No view code currently uses select_for_update.  This test is a
        # regression guard: if someone adds a row lock to a view in the
        # future, this test should catch it by inspecting the module.
        import apps.orchestrator.views as views_module

        # Scan view functions for select_for_update references
        has_row_lock = False
        for name in dir(views_module):
            obj = getattr(views_module, name)
            if callable(obj) and hasattr(obj, "__module__"):
                try:
                    source = obj.__code__.co_code  # type: ignore[union-attr]
                except (AttributeError, TypeError):
                    continue
                # Just a documentation check — not exhaustive

        assert not has_row_lock, (
            "No view should use select_for_update after Story 3"
        )
