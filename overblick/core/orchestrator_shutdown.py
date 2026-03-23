"""
Orchestrator shutdown — graceful teardown of all components.
"""

import logging

from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices

logger = logging.getLogger(__name__)


class OrchestratorShutdown:
    """Handles graceful shutdown of orchestrator components."""

    def __init__(
        self,
        *,
        services: OrchestratorServices,
        runtime_state: OrchestratorRuntimeState,
    ) -> None:
        """
        Args:
            services: Container with all framework services
            runtime_state: Runtime state container
        """
        self._services = services
        self._runtime_state = runtime_state

    async def shutdown(self) -> None:
        """Execute the full shutdown sequence."""
        from overblick.core.orchestrator_types import OrchestratorState

        logger.info("Orchestrator stopping...")

        # Stop scheduler
        if self._services.scheduler:
            await self._services.scheduler.stop()

        # Stop background cleanups
        if self._services.audit_log:
            self._services.audit_log.stop_background_cleanup()
        if self._services.engagement_db:
            self._services.engagement_db.stop_background_cleanup()

        # Teardown plugins (reverse order)
        for plugin in reversed(self._runtime_state.plugins):
            try:
                await plugin.teardown()
                logger.info("Plugin '%s' torn down", plugin.name)
            except Exception as e:
                logger.error("Error tearing down '%s': %s", plugin.name, e, exc_info=True)

        # Close LLM client
        if self._services.llm_client and hasattr(self._services.llm_client, "close"):
            try:
                await self._services.llm_client.close()
            except Exception as e:
                logger.error("Error closing LLM client: %s", e, exc_info=True)

        # Close engagement DB backend
        if self._services.engagement_db_backend:
            try:
                await self._services.engagement_db_backend.close()
            except Exception as e:
                logger.error("Error closing engagement DB backend: %s", e, exc_info=True)

        # Final audit log
        if self._services.audit_log:
            self._services.audit_log.log("orchestrator_stopped", category="lifecycle")
            self._services.audit_log.close()

        # Cleanup event bus
        if self._services.event_bus:
            self._services.event_bus.clear()

        self._runtime_state.lifecycle_state = OrchestratorState.STOPPED
        logger.info("Orchestrator stopped cleanly")
