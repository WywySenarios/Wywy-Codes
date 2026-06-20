"""Tests for FIFO cross-process signaling in the orchestrator.

These tests verify that ``wake_orchestrator``, ``abort_pipeline``, and
``orchestrator_loop`` use a named pipe (FIFO) for cross-process signaling
instead of the current in-memory ``threading.Event`` and ``queue.Queue``.

Every test in this file is **RED** — it asserts FIFO-based behavior that
does not exist yet in the production code.  The tests will pass after
Story 1 (Green) implements:
  - ``_SIGNAL_FIFO_PATH`` module constant
  - ``_ensure_signal_fifo()``
  - ``_pending_aborts: set[str]`` module variable
  - FIFO writes in ``wake_orchestrator()`` and ``abort_pipeline()``
  - ``select.select`` / ``os.read`` in ``orchestrator_loop()``
  - ``_pending_aborts`` check in ``_handle_stage_failure()``
"""

from __future__ import annotations

import errno
import os
from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from apps.orchestrator import orchestrator


class BreakLoop(BaseException):
    """Raised by the monkeypatched _wake_event.wait to break out of the
    orchestrator loop's ``while True`` during tests.  Inherits from
    ``BaseException`` (not ``Exception``) so it is never accidentally
    caught by a bare ``except Exception`` in the production code."""



from apps.orchestrator.models import Pipeline, PipelineStage

# The FIFO path that the orchestrator will use after Story 1 (Green).
# Defined as a test constant so it can change independently of the
# production constant (_SIGNAL_FIFO_PATH) without coupling the tests
# to a specific value.
FIFO_PATH = "/tmp/orchestrator_signals.fifo"

# Expected flags for FIFO writes — must be O_WRONLY | O_NONBLOCK
# so the write never blocks even when no reader is listening.
FIFO_WRITE_FLAGS = os.O_WRONLY | os.O_NONBLOCK


# ── wake_orchestrator ────────────────────────────────────────────────────


class TestWakeOrchestratorWritesToFifo:
    """``wake_orchestrator`` must write ``"wake\\n"`` to the FIFO.

    Currently the function calls ``_wake_event.set()``, which is an
    in-process signal that does not reach other Gunicorn workers.
    After Green it will open the FIFO and write the wake byte.
    """

    def test_writes_wake_signal_to_fifo(self, monkeypatch: MonkeyPatch) -> None:
        """``wake_orchestrator`` must write ``"wake\\n"`` to the FIFO.

        This test monkeypatches ``os.open`` and ``os.write`` on the
        orchestrator module to capture calls.  Currently the function
        does NOT use these calls (it sets ``_wake_event`` instead),
        so the assertions on ``os.open`` / ``os.write`` will fail.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        open_calls: list[tuple[str, int, int]] = []
        write_calls: list[tuple[int, bytes]] = []

        def track_open(
            path: str, flags: int, mode: int = 0o644
        ) -> int:
            open_calls.append((path, flags, mode))
            return 42  # fake fd

        def track_write(fd: int, data: bytes) -> int:
            write_calls.append((fd, data))
            return len(data)

        monkeypatch.setattr(orchestrator.os, "open", track_open)
        monkeypatch.setattr(orchestrator.os, "write", track_write)

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator.wake_orchestrator()

        # ── Assert ───────────────────────────────────────────────────────
        # These will fail *on behaviour* because wake_orchestrator()
        # currently sets _wake_event.set() instead of writing to a FIFO.
        assert len(open_calls) == 1, (
            f"Expected os.open to be called once, got {len(open_calls)} "
            f"(wake_orchestrator currently uses _wake_event.set())"
        )
        assert open_calls[0][0] == FIFO_PATH, (
            f"Expected os.open path '{FIFO_PATH}', "
            f"got '{open_calls[0][0]}'"
        )
        assert open_calls[0][1] == FIFO_WRITE_FLAGS, (
            f"Expected os.open flags O_WRONLY|O_NONBLOCK "
            f"({FIFO_WRITE_FLAGS}), got {open_calls[0][1]}"
        )
        assert len(write_calls) == 1, (
            f"Expected os.write to be called once, got {len(write_calls)}"
        )
        assert write_calls[0][1] == b"wake\n", (
            f"Expected os.write data b'wake\\n', "
            f"got {write_calls[0][1]!r}"
        )

    def test_write_to_fifo_without_reader_does_not_crash(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        """Writing to the FIFO when no reader is listening must not raise.

        This is critical for the WSGI-thread callers
        (``abort_pipeline`` in the view layer) — they must never crash
        even if the orchestrator worker has not yet started (or is busy).
        The ``O_NONBLOCK`` flag on the write side guarantees this.

        The current ``wake_orchestrator`` does not touch the filesystem
        at all, so it cannot experience this failure mode.  After Green
        the ``O_NONBLOCK`` flag protects against a missing reader.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Simulate a FIFO that exists but has no reader: os.write will
        # raise BrokenPipeError (or BlockingIOError with O_NONBLOCK).
        write_calls: list[bytes] = []

        def track_write(fd: int, data: bytes) -> int:
            write_calls.append(data)
            # With O_NONBLOCK, no reader yields BlockingIOError on some
            # systems, but a FIFO without a reader can also cause
            # BrokenPipeError if the other end was closed.  Either way
            # the function must handle it gracefully.
            raise BlockingIOError(
                errno.EAGAIN,
                "Resource temporarily unavailable — no reader on FIFO",
            )

        monkeypatch.setattr(orchestrator.os, "open", lambda p, f, m=0: 42)
        monkeypatch.setattr(orchestrator.os, "write", track_write)

        # ── Act — must not raise ─────────────────────────────────────────
        try:
            orchestrator.wake_orchestrator()
        except Exception as exc:
            pytest.fail(
                f"wake_orchestrator must not raise when FIFO has no reader, "
                f"got {type(exc).__name__}: {exc}"
            )

        # ── Assert — the write was attempted ─────────────────────────────
        assert len(write_calls) >= 1, (
            "At least one os.write call should have been attempted"
        )
        # After Green, the function must write "wake\\n" even if it
        # fails to deliver it.
        assert (
            b"wake\n" in write_calls
        ), f"Expected b'wake\\n' in attempted writes, got {write_calls!r}"


# ── abort_pipeline ───────────────────────────────────────────────────────


class TestAbortPipelineWritesToFifo:
    """``abort_pipeline`` must write ``"abort:<pipeline_id>\\n"`` to FIFO.

    Currently it puts the pipeline ID into ``_abort_queue`` and calls
    ``_stop_opencode_server`` before waking the orchestrator.  After
    Green it will write an abort signal to the FIFO and rely on the
    orchestrator to stop the server.
    """

    def test_writes_abort_signal_to_fifo(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_running: Pipeline,
    ) -> None:
        """``abort_pipeline`` must write ``"abort:<id>\\n"`` to FIFO.

        Also verifies the wake is sent after the abort signal (so the
        orchestrator processes it immediately).
        """
        # ── Arrange ──────────────────────────────────────────────────────
        open_calls: list[tuple[str, int, int]] = []
        write_calls: list[tuple[int, bytes]] = []
        close_calls: list[int] = []

        def track_open(
            path: str, flags: int, mode: int = 0o644
        ) -> int:
            fd = len(open_calls) + 100  # unique fds: 100, 101, ...
            open_calls.append((path, flags, mode))
            return fd

        def track_write(fd: int, data: bytes) -> int:
            write_calls.append((fd, data))
            return len(data)

        def track_close(fd: int) -> None:
            close_calls.append(fd)

        monkeypatch.setattr(orchestrator.os, "open", track_open)
        monkeypatch.setattr(orchestrator.os, "write", track_write)
        monkeypatch.setattr(orchestrator.os, "close", track_close)

        # Suppress side effects that would fail in test
        monkeypatch.setattr(
            orchestrator, "_stop_opencode_server", lambda p: None
        )

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator.abort_pipeline(pipeline_running)

        # ── Assert ───────────────────────────────────────────────────────
        # Must have written at least one FIFO message (the abort signal).
        # A second write may come from wake_orchestrator().
        assert len(open_calls) >= 1, (
            f"Expected os.open to be called at least once, "
            f"got {len(open_calls)} "
            f"(abort_pipeline currently uses _abort_queue.put())"
        )

        # At least one open must be for the FIFO with write flags
        fifo_opens = [
            oc for oc in open_calls
            if oc[0] == FIFO_PATH and oc[1] == FIFO_WRITE_FLAGS
        ]
        assert len(fifo_opens) >= 1, (
            f"Expected at least one os.open for '{FIFO_PATH}' "
            f"with O_WRONLY|O_NONBLOCK, got opens: {open_calls}"
        )

        # At least one write must carry the abort signal
        expected_abort = f"abort:{pipeline_running.id}\n".encode()
        abort_writes = [
            wc for wc in write_calls if wc[1] == expected_abort
        ]
        assert len(abort_writes) >= 1, (
            f"Expected os.write with {expected_abort!r}, "
            f"got writes: {[d for _, d in write_calls]}"
        )

    def test_still_calls_stop_opencode_server(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_running: Pipeline,
    ) -> None:
        """``abort_pipeline`` must still stop the opencode server.

        This is a safety-net test: the FIFO write is *additive* to the
        existing responsibility of stopping the agent container.
        """
        stop_calls: list[str] = []

        monkeypatch.setattr(
            orchestrator,
            "_stop_opencode_server",
            lambda p: stop_calls.append(str(p.id)),
        )
        monkeypatch.setattr(orchestrator.os, "open", lambda p, f, m=0: 42)
        monkeypatch.setattr(orchestrator.os, "write", lambda fd, d: len(d))
        monkeypatch.setattr(orchestrator.os, "close", lambda fd: None)

        orchestrator.abort_pipeline(pipeline_running)

        assert str(pipeline_running.id) in stop_calls, (
            f"_stop_opencode_server must be called for pipeline "
            f"{pipeline_running.id}, got calls: {stop_calls}"
        )


# ── orchestrator_loop ────────────────────────────────────────────────────


class TestOrchestratorLoopFifoSetup:
    """The orchestrator loop must open the FIFO for signal reading.

    After Green, ``orchestrator_loop()`` will:
      1. Call ``_ensure_signal_fifo()`` on startup.
      2. Open the FIFO with ``os.O_RDONLY | os.O_NONBLOCK``.
      3. Use ``select.select([fifo_fd], [], [], 1.0)`` instead of
         ``_wake_event.wait(1)``.
    """

    def test_loop_opens_fifo_for_reading(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The orchestrator loop must open the FIFO for reading.

        Currently the loop uses ``_wake_event.wait(timeout=1)`` and
        never touches any FIFO.  After Green, the FIFO replaces the
        in-process ``threading.Event``.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        open_calls: list[tuple[str, int, int]] = []

        def track_open(
            path: str, flags: int, mode: int = 0o644
        ) -> int:
            open_calls.append((path, flags, mode))
            return -1

        monkeypatch.setattr(orchestrator.os, "open", track_open)

        # Prevent side effects that would crash or hang
        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_transition_pipeline_state", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        # Break out of the while True after the first iteration.
        # _wake_event.wait is OUTSIDE the loop's try/except, so the
        # raised exception propagates cleanly up to the caller.
        def break_loop(timeout: float = 1.0) -> None:
            raise BreakLoop("break orcherator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(BreakLoop, match="break orcherator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        # With current code, no os.open calls occur.  This assertion
        # will fail until Green implements the FIFO.
        assert any(
            path == FIFO_PATH and (flags & os.O_ACCMODE) == os.O_RDONLY
            for path, flags, _mode in open_calls
        ), (
            f"orchestrator_loop must open '{FIFO_PATH}' for reading. "
            f"os.open calls: {open_calls}"
        )

    def test_fifo_created_via_mkfifo_at_loop_start(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The loop must create the FIFO via ``os.mkfifo`` at startup.

        Before entering the polling loop, the orchestrator ensures the
        FIFO exists so that ``wake_orchestrator`` / ``abort_pipeline``
        (which run in other Gunicorn workers) have a valid path to
        write to.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        mkfifo_calls: list[str] = []

        def track_mkfifo(path: str, mode: int = 0o644) -> None:
            mkfifo_calls.append(path)

        monkeypatch.setattr(orchestrator.os, "mkfifo", track_mkfifo)

        def track_open(path: str, flags: int, mode: int = 0o644) -> int:
            return -1

        monkeypatch.setattr(orchestrator.os, "open", track_open)

        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )

        def break_loop(timeout: float = 1.0) -> None:
            raise BreakLoop("break orcherator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(BreakLoop, match="break orcherator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        # Current code does not call os.mkfifo.
        assert len(mkfifo_calls) >= 1, (
            f"Expected os.mkfifo to be called at least once, "
            f"got {len(mkfifo_calls)} "
            f"(loop currently does not create a FIFO)"
        )
        assert mkfifo_calls[0] == FIFO_PATH, (
            f"Expected os.mkfifo path '{FIFO_PATH}', "
            f"got '{mkfifo_calls[0]}'"
        )


class TestOrchestratorLoopProcessesAbortFromFifo:
    """When the loop reads an abort signal from the FIFO, it must add the
    pipeline ID to ``_pending_aborts`` and subsequently cancel the pipeline.
    """

    def test_loop_reads_abort_and_cancels_queued_pipeline(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_queued: Pipeline,
    ) -> None:
        """A queued pipeline whose ID appears in the FIFO must be
        cancelled, not started.

        Currently the loop checks ``_check_and_consume_abort()`` which
        drains ``_abort_queue`` — an in-memory queue that is empty in
        a forked worker.  After Green, the loop checks ``_pending_aborts``
        which is populated by reading the FIFO at the top of each
        iteration.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_execute_pipeline", lambda p: None
        )
        monkeypatch.setattr(
            orchestrator, "_transition_pipeline_state",
            lambda p, s, **kw: setattr(p, "status", s) or p.save(update_fields=["status"]),
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            orchestrator, "_stop_opencode_server", lambda p: None
        )

        # Simulate that the FIFO populated _pending_aborts.
        # ``raising=False`` because ``_pending_aborts`` does not exist yet.
        monkeypatch.setattr(
            orchestrator, "_pending_aborts",
            {str(pipeline_queued.id)},
            raising=False,
        )

        # Provide a select mock that returns immediately.
        # ``raising=False`` is needed because ``select`` is not yet
        # imported in ``orchestrator.py`` — it will be added during Green.
        monkeypatch.setattr(
            orchestrator, "select", mock.MagicMock(), raising=False,
        )
        orchestrator.select.select = lambda r, w, e, t: (r, [], [])  # type: ignore[attr-defined]

        # Track os.open so the loop can "open" the FIFO
        monkeypatch.setattr(
            orchestrator.os, "open",
            lambda p, f, m=0: 42,
        )

        # Break out after first iteration
        def break_loop(timeout: float = 1.0) -> None:
            raise BreakLoop("break orcherator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(BreakLoop, match="break orcherator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        pipeline_queued.refresh_from_db()
        assert pipeline_queued.status == "cancelled", (
            f"Queued pipeline should be cancelled when abort signal "
            f"is in _pending_aborts, got '{pipeline_queued.status}' "
            f"(current code checks _abort_queue, not _pending_aborts)"
        )


# ── _handle_stage_failure ────────────────────────────────────────────────


class TestHandleStageFailureChecksPendingAborts:
    """``_handle_stage_failure`` must check ``_pending_aborts`` instead of
    ``_check_and_consume_abort`` (which drained ``_abort_queue``).

    After Green, Guard 1 in ``_handle_stage_failure`` will be::

        if str(pipeline.id) in _pending_aborts:
            _pending_aborts.discard(str(pipeline.id))
            stage.status = "failed"
            ...
            _transition_pipeline_state(pipeline, "cancelled")
            ...
    """

    def test_cancels_pipeline_when_id_in_pending_aborts(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
        pipeline_running: Pipeline,
    ) -> None:
        """When the pipeline ID is in ``_pending_aborts``, stage failure must
        cancel the pipeline instead of scheduling a retry.

        The current implementation calls ``_check_and_consume_abort()``
        which reads ``_abort_queue`` — an in-memory queue empty in a
        forked worker.  After Green, the function checks ``_pending_aborts``
        which is populated by FIFO reads in the orchestrator loop.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Use the existing RED stage from the pipeline_running fixture.
        # The fixture creates RED with status="running" and retry_count=0.
        stage = pipeline_running.stages.get(name="RED")

        # Inject _pending_aborts into the module — it doesn't exist yet.
        # ``raising=False`` is required because the attribute is new.
        monkeypatch.setattr(
            orchestrator,
            "_pending_aborts",
            {str(pipeline_running.id)},
            raising=False,
        )

        # Suppress side effects
        monkeypatch.setattr(
            orchestrator, "_teardown_workspace", lambda p: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator._handle_stage_failure(pipeline_running, stage)

        # ── Assert ───────────────────────────────────────────────────────
        stage.refresh_from_db()
        pipeline_running.refresh_from_db()

        assert stage.status == "failed", (
            f"Stage should be 'failed' when pipeline is in "
            f"_pending_aborts, got '{stage.status}'"
        )
        assert pipeline_running.status == "cancelled", (
            f"Pipeline should be 'cancelled' when in _pending_aborts, "
            f"got '{pipeline_running.status}' "
            f"(current code checks _abort_queue, not _pending_aborts)"
        )
        # Pipeline ID must be removed from _pending_aborts after processing
        assert str(pipeline_running.id) not in orchestrator._pending_aborts, (
            "Pipeline ID must be removed from _pending_aborts after "
            "being processed"
        )



    def test_consumes_abort_when_pipeline_already_terminal(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Even when the pipeline is already in a terminal state, the
        abort signal must be consumed (removed from ``_pending_aborts``)
        to prevent the signal from being processed twice.

        This matches the current behaviour of ``_check_and_consume_abort``
        which returns ``True`` when it finds a matching abort — the
        orchestrator treats the abort as acknowledged even if the
        pipeline is already dead.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        cancelled_pipeline = Pipeline.objects.create(
            invocation_name="terminal-test",
            status="cancelled",
        )

        # ``raising=False`` because ``_pending_aborts`` does not exist yet.
        monkeypatch.setattr(
            orchestrator,
            "_pending_aborts",
            {str(cancelled_pipeline.id)},
            raising=False,
        )
        monkeypatch.setattr(
            orchestrator, "_teardown_workspace", lambda p: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        stage = PipelineStage.objects.create(
            pipeline=cancelled_pipeline,
            name="init",
            status="running",
            retry_count=0,
        )

        # ── Act ──────────────────────────────────────────────────────────
        orchestrator._handle_stage_failure(cancelled_pipeline, stage)

        # ── Assert ───────────────────────────────────────────────────────
        # The abort signal must be consumed from _pending_aborts regardless
        cancelled_pipeline.refresh_from_db()
        assert cancelled_pipeline.status == "cancelled"
        assert str(cancelled_pipeline.id) not in orchestrator._pending_aborts, (
            "Pipeline ID must be removed from _pending_aborts even when "
            "pipeline is already terminal"
        )


    def test_multiple_signals_in_single_read_are_all_processed(
        self,
        db: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """If the FIFO contains multiple signals in one ``os.read`` call,
        all must be processed.

        This covers the scenario where two workers write to the FIFO
        within the same kernel buffer flush — the orchestrator reads
        both lines in one ``os.read(4096)`` and must handle each.
        """
        # ── Arrange ──────────────────────────────────────────────────────
        # Simulate readable FIFO with batched signals
        batched_data = b"wake\nabort:pipeline-a\nabort:pipeline-b\nwake\n"

        monkeypatch.setattr(orchestrator.os, "open", lambda p, f, m=0: 42)
        monkeypatch.setattr(orchestrator.os, "read", lambda fd, n: batched_data)

        # ``raising=False`` because ``_pending_aborts`` does not exist yet.
        monkeypatch.setattr(
            orchestrator, "_pending_aborts", set(), raising=False,
        )

        monkeypatch.setattr(
            orchestrator, "_ensure_agent_network", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_reap_orphaned_pipelines", lambda: None
        )
        monkeypatch.setattr(
            orchestrator, "_transition_pipeline_state", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            orchestrator, "_write_orchestrator_log", lambda *a, **kw: None
        )

        # Provide a select that returns immediately with readable FIFO.
        # ``raising=False`` because ``select`` is not yet imported in
        # ``orchestrator.py`` — it will be added during Green.
        monkeypatch.setattr(
            orchestrator, "select", mock.MagicMock(), raising=False,
        )
        orchestrator.select.select = lambda r, w, e, t: (r, [], [])  # type: ignore[attr-defined]

        def break_loop(timeout: float = 1.0) -> None:
            raise BreakLoop("break orcherator loop")

        monkeypatch.setattr(
            orchestrator._wake_event, "wait", break_loop
        )

        # ── Act ──────────────────────────────────────────────────────────
        with pytest.raises(BreakLoop, match="break orcherator loop"):
            orchestrator.orchestrator_loop()

        # ── Assert ───────────────────────────────────────────────────────
        # After Green, both abort signals should be in _pending_aborts
        pending = orchestrator._pending_aborts
        assert "pipeline-a" in pending, (
            "First abort signal must be in _pending_aborts"
        )
        assert "pipeline-b" in pending, (
            "Second abort signal must be in _pending_aborts"
        )
