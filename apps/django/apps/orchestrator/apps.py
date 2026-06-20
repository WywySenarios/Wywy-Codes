"""Django AppConfig for the orchestrator that starts the pipeline lifecycle thread.

Uses an ``fcntl.flock``-based file lock to ensure exactly one Gunicorn
worker starts the orchestrator daemon thread.  Other workers serve HTTP
requests only.

Because ``ready()`` is called in every forked worker, the lock is acquired
with ``LOCK_EX | LOCK_NB``.  The first worker to call ``ready()`` acquires
the lock and becomes the orchestrator; subsequent workers skip it.

The lock file descriptor is stored on the config instance inside the
``ready()`` method's local scope.  Since ``ready()`` is called during
Django setup and the config instance lives for the process lifetime, the
fd stays open and the kernel releases the lock on process exit.

.. warning::

   ``--preload`` must NOT be used with Gunicorn.  If ``ready()`` runs in
   the master process before fork, all children inherit the lock fd and
   all of them would start an orchestrator thread, defeating the lock.
"""

import collections.abc
import fcntl
import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Path to the file lock used to elect a single orchestrator worker.
# The first worker to open and flock this file becomes the orchestrator;
# all other workers skip the orchestrator thread.
_ORCHESTRATOR_LOCK_PATH: str = "/tmp/orchestrator.lock"


class OrchestratorConfig(AppConfig):
    """Orchestrator app configuration.

    Starts the pipeline lifecycle daemon thread in exactly one Gunicorn
    worker by using ``fcntl.flock()`` as a cross-process mutex.

    **Gunicorn constraint:** ``--preload`` must NOT be used.  Gunicorn's
    preload mode runs ``ready()`` in the master process before fork,
    causing all children to inherit the file lock.  Every worker would
    then start an orchestrator thread, defeating the lock.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orchestrator"

    def ready(self) -> None:
        if os.environ.get("ENVIRONMENT") == "test":
            logger.info("ENVIRONMENT=test - skipping orchestrator loop thread")
            return

        # ── Acquire file lock to elect a single orchestrator worker ─────
        # Only the worker that holds this lock starts the orchestrator
        # thread.  The lock is released when the process exits (kernel
        # automatically cleans up file descriptors).
        try:
            lock_fd = os.open(
                _ORCHESTRATOR_LOCK_PATH,
                os.O_CREAT | os.O_RDWR,
                0o644,
            )
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            logger.info(
                "Orchestrator lock held by another worker — "
                "skipping orchestrator thread"
            )
            return

        # Store the fd on the config instance to keep it open for the
        # process lifetime.  The kernel releases the flock on process exit.
        self._lock_fd = lock_fd
        logger.info("Orchestrator lock acquired — starting orchestrator")

        from apps.orchestrator.orchestrator import orchestrator_loop

        logger.info("Starting orchestrator loop thread")

        thread = threading.Thread(
            target=_run_orchestrator_loop,
            args=[orchestrator_loop],
            daemon=True,
            name="orchestrator-loop",
        )
        thread.start()
        logger.info("Orchestrator loop thread started (tid=%s)", thread.native_id)


def _run_orchestrator_loop(loop_fn: collections.abc.Callable[[], None]) -> None:
    try:
        loop_fn()
    except Exception:
        logger.exception("Orchestrator loop thread crashed")
    else:
        logger.info("Orchestrator loop thread finished")
