"""
Orchestrator runtime — scheduling, heartbeat, and main loop.
"""

import asyncio
import logging
from typing import Any

from overblick.core.scheduler import TaskPriority

logger = logging.getLogger(__name__)


class OrchestratorRuntime:
    """Handles the main runtime loop of the orchestrator."""

    def __init__(self, orchestrator: Any):
        """
        Args:
            orchestrator: The Orchestrator instance whose components are already set up.
        """
        self._orch = orchestrator

    async def run(self) -> None:
        """Execute the main runtime loop (blocks until shutdown signal)."""
        # Register signal handlers — cross-platform (Unix signals / Windows signal.signal)
        from overblick.shared.platform import register_shutdown_signals

        register_shutdown_signals(self._orch._shutdown_event)

        self._orch._audit_log.log("orchestrator_started", category="lifecycle")
        self._orch._audit_log.start_background_cleanup()

        if self._orch._engagement_db:
            self._orch._engagement_db.start_background_cleanup()

        logger.info(f"Överblick orchestrator running as '{self._orch._identity.display_name}'")
        print(f"\n  [ Överblick ] {self._orch._identity.display_name} is awake.\n")

        try:
            # Register plugin ticks in scheduler (guarded by control file)
            for plugin in self._orch._plugins:
                interval = self._orch._identity.schedule.feed_poll_minutes * 60

                async def _guarded_tick(p=plugin):
                    import time as _time

                    logger.debug("Guarded tick starting for '%s'", p.name)
                    if await self._orch._is_plugin_stopped(p.name):
                        logger.debug("Agent '%s' stopped via control file, skipping tick", p.name)
                        return
                    tick_start = _time.monotonic()
                    await p.tick()
                    tick_ms = (_time.monotonic() - tick_start) * 1000
                    logger.debug("Guarded tick completed for '%s' (%.1fms)", p.name, tick_ms)
                    await self._orch._event_bus.emit(
                        "plugin_tick",
                        plugin=p.name,
                        identity=self._orch._identity_name,
                        duration_ms=tick_ms,
                    )

                await self._orch._scheduler.add(
                    f"tick_{plugin.name}",
                    _guarded_tick,
                    interval_seconds=interval,
                    run_immediately=True,
                    priority=TaskPriority.LOW,
                )

                # Schedule heartbeat if plugin supports it (e.g. MoltbookPlugin)
                if callable(getattr(plugin, "post_heartbeat", None)):
                    heartbeat_interval = self._orch._identity.schedule.heartbeat_hours * 3600

                    async def _guarded_heartbeat(p=plugin):
                        if await self._orch._is_plugin_stopped(p.name):
                            return
                        await p.post_heartbeat()

                    await self._orch._scheduler.add(
                        f"heartbeat_{plugin.name}",
                        _guarded_heartbeat,
                        interval_seconds=heartbeat_interval,
                        run_immediately=False,
                        priority=TaskPriority.HIGH,
                    )
                    logger.info(
                        "Heartbeat scheduled for '%s' every %dh",
                        plugin.name,
                        self._orch._identity.schedule.heartbeat_hours,
                    )

            # Run scheduler and shutdown event concurrently — first to complete wins
            scheduler_task = asyncio.create_task(self._orch._scheduler.start())
            shutdown_task = asyncio.create_task(self._orch._shutdown_event.wait())

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
            # Delegate shutdown to the orchestrator's stop method
            await self._orch.stop()
