"""
Task scheduler — periodic and one-shot async tasks.

Provides a simple way for plugins to register recurring work
(e.g., feed polling, heartbeat posting) without managing their own timers.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

logger = logging.getLogger(__name__)

TaskFunc = Callable[..., Coroutine[Any, Any, None]]


class ScheduledTask(BaseModel):
    """A registered scheduled task."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    func: TaskFunc
    interval_seconds: float
    last_run: float = 0.0
    run_count: int = 0
    error_count: int = 0
    enabled: bool = True
    run_immediately: bool = False
    _task: Optional[asyncio.Task] = PrivateAttr(default=None)


class Scheduler:
    """
    Async task scheduler for periodic work.

    Usage:
        scheduler = Scheduler()
        scheduler.add("poll_feed", my_poll_func, interval_seconds=300)
        await scheduler.start()  # runs until stop() is called
    """

    def __init__(self):
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False

    def add(
        self,
        name: str,
        func: TaskFunc,
        interval_seconds: float,
        run_immediately: bool = False,
    ) -> None:
        """
        Register a periodic task.

        Args:
            name: Unique task name
            func: Async function to call
            interval_seconds: Seconds between invocations
            run_immediately: Run once immediately on start
        """
        if name in self._tasks:
            raise ValueError(f"Task '{name}' already registered")

        self._tasks[name] = ScheduledTask(
            name=name,
            func=func,
            interval_seconds=interval_seconds,
            run_immediately=run_immediately,
        )
        logger.debug(f"Scheduler: registered '{name}' every {interval_seconds}s")

    def remove(self, name: str) -> bool:
        """Remove a task. Returns True if found."""
        task = self._tasks.pop(name, None)
        if task and task._task:
            task._task.cancel()
        return task is not None

    async def start(self) -> None:
        """Start all scheduled tasks. Blocks until stop() is called."""
        self._running = True
        logger.info(f"Scheduler starting with {len(self._tasks)} tasks")

        # Create asyncio tasks for each scheduled task
        for st in self._tasks.values():
            st._task = asyncio.create_task(self._run_loop(st))

        # Wait until stopped
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop all scheduled tasks (idempotent — safe to call multiple times)."""
        if not self._running:
            return
        self._running = False
        for st in list(self._tasks.values()):
            if st._task and not st._task.done():
                st._task.cancel()
                try:
                    await st._task
                except asyncio.CancelledError:
                    pass
        logger.info("Scheduler stopped")

    async def _run_loop(self, st: ScheduledTask) -> None:
        """Run a single task's scheduling loop."""
        # Handle run_immediately
        if st.run_immediately:
            await self._execute(st)

        while self._running and st.enabled:
            try:
                await asyncio.sleep(st.interval_seconds)
                if self._running and st.enabled:
                    await self._execute(st)
            except asyncio.CancelledError:
                break
            except Exception as e:
                st.error_count += 1
                logger.error(f"Scheduler: '{st.name}' error: {e}", exc_info=True)
                await asyncio.sleep(min(st.interval_seconds, 60))

    async def _execute(self, st: ScheduledTask) -> None:
        """Execute a scheduled task with error handling."""
        try:
            await st.func()
            st.last_run = time.time()
            st.run_count += 1
        except Exception as e:
            st.error_count += 1
            logger.error(f"Scheduler: '{st.name}' execution error: {e}", exc_info=True)

    def get_stats(self) -> dict[str, dict]:
        """Get statistics for all tasks."""
        return {
            name: {
                "interval_seconds": st.interval_seconds,
                "run_count": st.run_count,
                "error_count": st.error_count,
                "enabled": st.enabled,
                "last_run": st.last_run,
            }
            for name, st in self._tasks.items()
        }

    @property
    def task_count(self) -> int:
        return len(self._tasks)
