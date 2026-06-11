"""Django AppConfig for the orchestrator that starts the pipeline lifecycle thread."""

import collections.abc
import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class OrchestratorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orchestrator"

    def ready(self) -> None:
        if os.environ.get("ENVIRONMENT") == "test":
            logger.info("ENVIRONMENT=test - skipping orchestrator loop thread")
            return

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
