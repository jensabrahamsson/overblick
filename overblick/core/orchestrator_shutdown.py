"""
Orchestrator shutdown — graceful teardown of all components.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class OrchestratorShutdown:
    """Handles graceful shutdown of orchestrator components."""

    def __init__(self, orchestrator: Any):
        self._orch = orchestrator

    async def shutdown(self) -> None:
        """Execute the full shutdown sequence (idempotent)."""
        if self._orch._state == self._orch._state.STOPPING:
            return  # Prevent double-stop (already in progress)

        self._orch._state = self._orch._state.STOPPING
        logger.info("Orchestrator stopping...")

        # Stop scheduler
        await self._orch._scheduler.stop()

        # Stop background cleanups
        if self._orch._audit_log:
            self._orch._audit_log.stop_background_cleanup()
        if self._orch._engagement_db:
            self._orch._engagement_db.stop_background_cleanup()

        # Teardown plugins (reverse order)
        for plugin in reversed(self._orch._plugins):
            try:
                await plugin.teardown()
                logger.info(f"Plugin '{plugin.name}' torn down")
            except Exception as e:
                logger.error(f"Error tearing down '{plugin.name}': {e}", exc_info=True)

        # Close LLM client
        if self._orch._llm_client and hasattr(self._orch._llm_client, "close"):
            try:
                await self._orch._llm_client.close()
            except Exception as e:
                logger.error(f"Error closing LLM client: {e}", exc_info=True)

        # Close engagement DB backend
        if self._orch._engagement_db_backend:
            try:
                await self._orch._engagement_db_backend.close()
            except Exception as e:
                logger.error("Error closing engagement DB backend: %s", e, exc_info=True)

        # Final audit log
        if self._orch._audit_log:
            self._orch._audit_log.log("orchestrator_stopped", category="lifecycle")
            self._orch._audit_log.close()

        # Cleanup event bus
        self._orch._event_bus.clear()

        self._orch._state = self._orch._state.STOPPED
        logger.info("Orchestrator stopped cleanly")
