"""
Task scheduler — periodic and one-shot async tasks.

Provides a simple way for plugins to register recurring work
(e.g., feed polling, heartbeat posting) without managing their own timers.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

logger = logging.getLogger(__name__)

TaskFunc = Callable[..., Coroutine[Any, Any, None]]


class TaskPriority(Enum):
    """Task execution priority levels."""

    LOW = "low"  # Background tasks (feed polling, housekeeping)
    HIGH = "high"  # Time-sensitive tasks (heartbeat, user notifications)


# Constants for backoff strategy
MAX_ERROR_BACKOFF_SECONDS: float = 60.0
MIN_RECOVERY_INTERVAL_SECONDS: float = 5.0
BACKOFF_MULTIPLIER: float = 2.0
MAX_BACKOFF_EXPONENT: int = 5


class ScheduledTask(BaseModel):
    """A registered scheduled task with priority support."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    func: TaskFunc
    interval_seconds: float
    last_run: float = 0.0
    run_count: int = 0
    error_count: int = 0
    enabled: bool = True
    run_immediately: bool = False
    priority: TaskPriority = TaskPriority.LOW
    _task: asyncio.Task | None = PrivateAttr(default=None)


class Scheduler:
    """
    Async task scheduler for periodic work with priority support.

    Usage:
        scheduler = Scheduler()
        await scheduler.add("poll_feed", my_poll_func, interval_seconds=300)
        await scheduler.start()  # runs until stop() is called

    Thread Safety: All shared state access is protected by asyncio locks.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._lock = asyncio.Lock()  # Protects concurrent task modifications

    async def add(
        self,
        name: str,
        func: TaskFunc,
        interval_seconds: float,
        run_immediately: bool = False,
        priority: TaskPriority = TaskPriority.LOW,
    ) -> None:
        """
        Register a periodic task (thread-safe).

        Args:
            name: Unique task name
            func: Async function to call
            interval_seconds: Seconds between invocations
            run_immediately: Run once immediately on start
            priority: Task execution priority (HIGH tasks executed first)

        Raises:
            ValueError: If task with same name already exists
        """
        async with self._lock:
            if name in self._tasks:
                raise ValueError(f"Task '{name}' already registered")

            self._tasks[name] = ScheduledTask(
                name=name,
                func=func,
                interval_seconds=interval_seconds,
                run_immediately=run_immediately,
                priority=priority,
            )

        logger.debug(
            f"Scheduler: registered '{name}' every {interval_seconds}s (priority: {priority.value})"
        )

    async def remove(self, name: str) -> bool:
        """Remove a task (thread-safe). Returns True if found."""
        async with self._lock:
            task = self._tasks.pop(name, None)

        if task and task._task:
            task._task.cancel()
            try:
                await task._task
            except asyncio.CancelledError:
                pass

        return task is not None

    async def get_task_stats(self) -> dict[str, dict]:
        """Get statistics for all tasks (thread-safe)."""
        async with self._lock:
            return {
                name: {
                    "interval_seconds": st.interval_seconds,
                    "run_count": st.run_count,
                    "error_count": st.error_count,
                    "enabled": st.enabled,
                    "last_run": st.last_run,
                    "priority": st.priority.value,
                }
                for name, st in self._tasks.items()
            }

    async def set_task_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a task (thread-safe). Returns True if found."""
        async with self._lock:
            if name not in self._tasks:
                return False

            self._tasks[name].enabled = enabled
            logger.info(f"Scheduler: task '{name}' {'enabled' if enabled else 'disabled'}")
            return True

    async def get_task_count(self) -> int:
        """Get total number of registered tasks (thread-safe)."""
        async with self._lock:
            return len(self._tasks)

    @property
    def task_count(self) -> int:
        """Get total number of registered tasks (non-atomic read)."""
        # Note: For atomic reads, use get_task_count() instead
        return len(self._tasks)

    async def start(self) -> None:
        """Start all scheduled tasks. Blocks until stop() is called."""
        self._running = True
        logger.info(f"Scheduler starting with {len(self._tasks)} tasks")

        # Create asyncio tasks for each scheduled task (sorted by priority)
        sorted_tasks = sorted(
            list(self._tasks.values()),
            key=lambda t: (t.priority == TaskPriority.HIGH, t.name),
        )

        async with self._lock:
            for st in sorted_tasks:
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

        async with self._lock:
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
        """Run a single task's scheduling loop with exponential backoff."""
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

                # Calculate exponential backoff after 2 consecutive failures
                if st.error_count >= 2:
                    exponent = min(st.error_count - 2, MAX_BACKOFF_EXPONENT)
                    backoff_seconds = MIN_RECOVERY_INTERVAL_SECONDS * (BACKOFF_MULTIPLIER**exponent)
                    actual_backoff = min(backoff_seconds, MAX_ERROR_BACKOFF_SECONDS)

                    logger.warning(
                        "Scheduler: '%s' failed %d times, backing off for %.1fs",
                        st.name,
                        st.error_count,
                        actual_backoff,
                    )
                    await asyncio.sleep(actual_backoff)
                else:
                    logger.error(f"Scheduler: '{st.name}' execution error: {e}", exc_info=True)

    async def _execute(self, st: ScheduledTask) -> None:
        """Execute a scheduled task with error handling and recovery."""
        try:
            await st.func()

            # Reset error count on success (with logging for recovery events)
            if st.error_count > 0:
                logger.info(
                    "Scheduler: '%s' recovered after %d consecutive errors",
                    st.name,
                    st.error_count,
                )
                st.error_count = 0

            st.last_run = time.time()
            st.run_count += 1

        except asyncio.CancelledError:
            # Re-raise cancellation without incrementing error count
            raise
        except Exception as e:
            st.error_count += 1
            logger.error(
                f"Scheduler: '{st.name}' execution error (attempt {st.error_count}): {e}",
                exc_info=True,
            )

    async def get_stats(self) -> dict[str, dict]:
        """Alias for get_task_stats() for backwards compatibility."""
        return await self.get_task_stats()
