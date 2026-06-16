"""Tests for orchestrator loop helpers."""

from __future__ import annotations

from unittest import mock

from _pytest.monkeypatch import MonkeyPatch
from django.db.utils import OperationalError

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline


class TestReapOrphanedPipelines:
    def test_handles_database_error_gracefully(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """_reap_orphaned_pipelines should survive a transient database error
        without crashing the orchestrator loop thread.

        When the database is temporarily unavailable (e.g. permissions
        issue, mount not ready), the function must not raise an unhandled
        OperationalError that kills the loop forever.
        """
        def broken_filter(*args, **kwargs):
            raise OperationalError("unable to open database file")

        monkeypatch.setattr(Pipeline.objects, "filter", broken_filter)

        # The current code lets OperationalError propagate — this will fail.
        orchestrator._reap_orphaned_pipelines()
