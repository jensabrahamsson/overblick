"""
Orchestrator runtime — scheduling, heartbeat, and main loop.
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable

from overblick.core.scheduler import TaskPriority
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices

logger = logging.getLogger(__name__)


class OrchestratorRuntime:
    """Handles the main runtime loop of the orchestrator."""

    def __init__(
        self,
        *,
        identity_name: str,
        services: OrchestratorServices,
        runtime_state: OrchestratorRuntimeState,
        on_stop_requested: Callable[[], Awaitable[None]],
        run_controller: Any = None,  # Will be PluginRunController later
    ) -> None:
        """
        Args:
            identity_name: Name of the identity being orchestrated
            services: Container with all framework services
            runtime_state: Runtime state container
            on_stop_requested: Async callback to invoke on shutdown request
            run_controller: Controller for plugin control-file logic (optional)
        """
        self._identity_name = identity_name
        self._services = services
        self._runtime_state = runtime_state
        self._on_stop_requested = on_stop_requested

    async def run(self) -> None:
        """Execute the main runtime loop (blocks until shutdown signal)."""
        # Register signal handlers — cross-platform (Unix signals / Windows signal.signal)
        from overblick.shared.platform import register_shutdown_signals

        register_shutdown_signals(self._runtime_state.shutdown_event)

        if self._services.audit_log:
            self._services.audit_log.log("orchestrator_started", category="lifecycle")
            self._services.audit_log.start_background_cleanup()

        if self._services.engagement_db:
            self._services.engagement_db.start_background_cleanup()

        if self._services.identity:
            logger.info(
                f"Överblick orchestrator running as '{self._services.identity.display_name}'"
            )
            print(f"\n  [ Överblick ] {self._services.identity.display_name} is awake.\n")

        try:
            # Register plugin ticks in scheduler (guarded by control file)
            for plugin in self._runtime_state.plugins:
                interval = self._services.identity.schedule.feed_poll_minutes * 60

                async def _guarded_tick(p=plugin):
                    import time as _time

                    logger.debug("Guarded tick starting for '%s'", p.name)
                    if await self._is_plugin_stopped(p.name):
                        logger.debug("Agent '%s' stopped via control file, skipping tick", p.name)
                        return
                    tick_start = _time.monotonic()
                    await p.tick()
                    tick_ms = (_time.monotonic() - tick_start) * 1000
                    logger.debug("Guarded tick completed for '%s' (%.1fms)", p.name, tick_ms)
                    if self._services.event_bus:
                        await self._services.event_bus.emit(
                            "plugin_tick",
                            plugin=p.name,
                            identity=self._identity_name,
                            duration_ms=tick_ms,
                        )

                await self._services.scheduler.add(
                    f"tick_{plugin.name}",
                    _guarded_tick,
                    interval_seconds=interval,
                    run_immediately=True,
                    priority=TaskPriority.LOW,
                )

                # Schedule heartbeat if plugin supports it (e.g. MoltbookPlugin)
                if callable(getattr(plugin, "post_heartbeat", None)):
                    heartbeat_interval = self._services.identity.schedule.heartbeat_hours * 3600

                    async def _guarded_heartbeat(p=plugin):
                        if await self._is_plugin_stopped(p.name):
                            return
                        await p.post_heartbeat()

                    await self._services.scheduler.add(
                        f"heartbeat_{plugin.name}",
                        _guarded_heartbeat,
                        interval_seconds=heartbeat_interval,
                        run_immediately=False,
                        priority=TaskPriority.HIGH,
                    )
                    logger.info(
                        "Heartbeat scheduled for '%s' every %dh",
                        plugin.name,
                        self._services.identity.schedule.heartbeat_hours,
                    )

            # Run scheduler and shutdown event concurrently — first to complete wins
            scheduler_task = asyncio.create_task(self._services.scheduler.start())
            shutdown_task = asyncio.create_task(self._runtime_state.shutdown_event.wait())

            _done, pending = await asyncio.wait(
                {scheduler_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            logger.info("Orchestrator cancelled")
        except Exception as e:
            logger.error(f"Orchestrator error: {e}", exc_info=True)
        finally:
            # Delegate shutdown
            await self._on_stop_requested()

    async def _is_plugin_stopped(self, plugin_name: str) -> bool:
        """Check if a plugin is stopped via control file."""
        if not self._runtime_state.control_file or not self._runtime_state.control_file.exists():
            return False

        # Use cache for performance
        now = time.time()
        if (
            self._runtime_state.control_cache_ts > 0
            and now - self._runtime_state.control_cache_ts < 2.0
        ):
            return self._runtime_state.control_cache.get(plugin_name, "false") == "true"

        try:
            import json

            with open(self._runtime_state.control_file, "r") as f:
                data = json.load(f)
                value = data.get(plugin_name, "false")
                self._runtime_state.control_cache[plugin_name] = value
                self._runtime_state.control_cache_ts = now
                return value == "true"
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return False


# Import time here to avoid circular import at module level
import time
