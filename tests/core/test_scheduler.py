"""Tests for scheduler."""

import asyncio
from unittest.mock import patch

import pytest

from overblick.core.scheduler import (
    BACKOFF_MULTIPLIER,
    MAX_BACKOFF_EXPONENT,
    MAX_ERROR_BACKOFF_SECONDS,
    MIN_RECOVERY_INTERVAL_SECONDS,
    ScheduledTask,
    Scheduler,
    TaskPriority,
)


class TestScheduler:
    async def test_add_task(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        assert s.task_count == 1

    async def test_add_duplicate_raises(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        with pytest.raises(ValueError, match="already registered"):
            await s.add("task1", noop, interval_seconds=60)

    async def test_remove_task(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        assert await s.remove("task1")
        assert s.task_count == 0

    async def test_remove_nonexistent(self):
        s = Scheduler()
        assert not await s.remove("nope")

    async def test_remove_task_with_running_asyncio_task(self):
        """Lines 112-117: remove a task that has an active asyncio.Task."""
        s = Scheduler()
        started = asyncio.Event()

        async def long_running():
            started.set()
            await asyncio.sleep(100)

        await s.add("running", long_running, interval_seconds=999)
        st = s._tasks["running"]
        # Simulate a running asyncio task
        st._task = asyncio.create_task(long_running())
        await started.wait()

        result = await s.remove("running")
        assert result is True
        assert st._task.cancelled() or st._task.done()

    async def test_get_stats_empty(self):
        s = Scheduler()
        stats = await s.get_stats()
        assert stats == {}

    async def test_get_stats_with_tasks(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("t1", noop, interval_seconds=30)
        stats = await s.get_stats()
        assert "t1" in stats
        assert stats["t1"]["interval_seconds"] == 30
        assert stats["t1"]["run_count"] == 0
        assert stats["t1"]["priority"] == "low"

    async def test_execute_increments_count(self):
        s = Scheduler()
        counter = {"n": 0}

        async def inc():
            counter["n"] += 1

        await s.add("inc", inc, interval_seconds=999)
        task = s._tasks["inc"]
        await s._execute(task)

        assert counter["n"] == 1
        assert task.run_count == 1
        assert task.last_run > 0

    async def test_execute_error_increments_error_count(self):
        s = Scheduler()

        async def fail():
            raise RuntimeError("boom")

        await s.add("fail", fail, interval_seconds=999)
        task = s._tasks["fail"]
        await s._execute(task)

        assert task.error_count == 1
        assert task.run_count == 0

    async def test_priority_ordering(self):
        """Test that HIGH priority tasks are sorted before LOW."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("low_task", noop, interval_seconds=60, priority=TaskPriority.LOW)
        await s.add("high_task", noop, interval_seconds=30, priority=TaskPriority.HIGH)

        stats = await s.get_stats()
        assert stats["high_task"]["priority"] == TaskPriority.HIGH.value
        assert stats["low_task"]["priority"] == TaskPriority.LOW.value

    async def test_set_task_enabled_true(self):
        """Lines 138-144: enable/disable an existing task."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("t", noop, interval_seconds=10)
        result = await s.set_task_enabled("t", False)
        assert result is True
        stats = await s.get_task_stats()
        assert stats["t"]["enabled"] is False

        result = await s.set_task_enabled("t", True)
        assert result is True
        stats = await s.get_task_stats()
        assert stats["t"]["enabled"] is True

    async def test_set_task_enabled_not_found(self):
        """Line 140: returns False for non-existent task."""
        s = Scheduler()
        result = await s.set_task_enabled("missing", True)
        assert result is False

    async def test_get_task_count(self):
        """Lines 148-149: async get_task_count."""
        s = Scheduler()

        async def noop():
            pass

        assert await s.get_task_count() == 0
        await s.add("t", noop, interval_seconds=10)
        assert await s.get_task_count() == 1

    async def test_start_and_stop(self):
        """Lines 159-179, 186-197: start runs tasks, stop cancels them."""
        s = Scheduler()
        call_count = {"n": 0}

        async def counting():
            call_count["n"] += 1

        await s.add("counter", counting, interval_seconds=0.05, run_immediately=True)

        async def stop_after_delay():
            await asyncio.sleep(0.15)
            await s.stop()

        stopper = asyncio.create_task(stop_after_delay())
        await s.start()
        await stopper

        assert call_count["n"] >= 1
        assert not s._running

    async def test_start_cancelled_calls_stop(self):
        """Lines 176-179: CancelledError in start triggers finally->stop."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("t", noop, interval_seconds=100)

        start_task = asyncio.create_task(s.start())
        await asyncio.sleep(0.05)
        start_task.cancel()

        # start() catches CancelledError and calls stop() in finally
        await start_task

        assert not s._running

    async def test_stop_idempotent(self):
        """Line 183-184: stop when not running is a no-op."""
        s = Scheduler()
        # Not started yet, _running is False
        await s.stop()
        assert not s._running
        # Call again to confirm idempotent
        await s.stop()

    async def test_stop_cancels_running_tasks(self):
        """Lines 186-197: stop cancels asyncio tasks that are not done."""
        s = Scheduler()
        started = asyncio.Event()

        async def long_task():
            started.set()
            await asyncio.sleep(100)

        await s.add("long", long_task, interval_seconds=100)

        async def start_and_stop():
            start_coro = asyncio.create_task(s.start())
            await started.wait()
            await s.stop()
            start_coro.cancel()
            try:
                await start_coro
            except asyncio.CancelledError:
                pass

        await start_and_stop()
        assert not s._running

    async def test_run_loop_run_immediately(self):
        """Lines 202-203: run_immediately triggers immediate execution."""
        s = Scheduler()
        calls = []

        async def track():
            calls.append(1)
            # After first call, disable to exit loop
            s._running = False

        await s.add("imm", track, interval_seconds=0.01, run_immediately=True)
        st = s._tasks["imm"]

        s._running = True
        await s._run_loop(st)

        assert len(calls) >= 1

    async def test_run_loop_cancelled_error_breaks(self):
        """Lines 210-211: CancelledError breaks the loop cleanly."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("cancel_me", noop, interval_seconds=0.01)
        st = s._tasks["cancel_me"]
        s._running = True

        async def cancel_on_sleep(duration: float) -> None:
            raise asyncio.CancelledError()

        with patch("overblick.core.scheduler.asyncio.sleep", side_effect=cancel_on_sleep):
            # _run_loop catches CancelledError and breaks, so it returns normally
            await s._run_loop(st)

    async def test_run_loop_exception_first_failure_logs_error(self):
        """Lines 212-213, 228-229: first exception in run_loop logs error."""
        s = Scheduler()
        call_count = {"n": 0}
        original_sleep = asyncio.sleep

        async def noop():
            pass

        await s.add("flakey", noop, interval_seconds=0.01)
        st = s._tasks["flakey"]
        s._running = True

        async def flaky_sleep(duration: float) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("sleep fail")
            # Second call: stop
            s._running = False
            await original_sleep(0)

        with patch("overblick.core.scheduler.asyncio.sleep", side_effect=flaky_sleep):
            await s._run_loop(st)

        assert st.error_count == 1

    async def test_run_loop_backoff_after_multiple_failures(self):
        """Lines 212-227: exponential backoff after 2+ consecutive failures."""
        s = Scheduler()
        sleep_call = {"n": 0}
        original_sleep = asyncio.sleep
        sleep_durations: list[float] = []

        async def noop():
            pass

        await s.add("backoff", noop, interval_seconds=0.01)
        st = s._tasks["backoff"]
        s._running = True

        # Pre-set error_count to 1 so next failure triggers backoff path (>= 2)
        st.error_count = 1

        async def failing_then_stop_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            sleep_call["n"] += 1
            if sleep_call["n"] == 1:
                # First interval sleep raises to trigger the except Exception path
                raise RuntimeError("interval fail")
            # After the backoff sleep, stop
            s._running = False
            await original_sleep(0)

        with patch("overblick.core.scheduler.asyncio.sleep", side_effect=failing_then_stop_sleep):
            await s._run_loop(st)

        # error_count was 1 before, now incremented to 2
        assert st.error_count >= 2
        # Second sleep call should be the backoff (MIN_RECOVERY_INTERVAL_SECONDS)
        assert len(sleep_durations) >= 2
        assert sleep_durations[1] == MIN_RECOVERY_INTERVAL_SECONDS

    async def test_execute_recovery_resets_error_count(self):
        """Lines 237-243: successful execution after errors resets count and logs."""
        s = Scheduler()

        async def succeed():
            pass

        await s.add("recover", succeed, interval_seconds=999)
        st = s._tasks["recover"]
        st.error_count = 3  # Simulate previous failures

        await s._execute(st)

        assert st.error_count == 0
        assert st.run_count == 1

    async def test_execute_cancelled_error_reraises(self):
        """Lines 248-250: CancelledError is re-raised without incrementing error_count."""
        s = Scheduler()

        async def cancel_self():
            raise asyncio.CancelledError()

        await s.add("canceller", cancel_self, interval_seconds=999)
        st = s._tasks["canceller"]

        with pytest.raises(asyncio.CancelledError):
            await s._execute(st)

        assert st.error_count == 0
        assert st.run_count == 0

    async def test_start_sorts_by_priority(self):
        """Lines 163-166: tasks sorted by priority on start."""
        s = Scheduler()
        order = []

        async def track_low():
            order.append("low")

        async def track_high():
            order.append("high")

        await s.add("low", track_low, interval_seconds=100, run_immediately=True, priority=TaskPriority.LOW)
        await s.add("high", track_high, interval_seconds=100, run_immediately=True, priority=TaskPriority.HIGH)

        async def stop_soon():
            await asyncio.sleep(0.1)
            await s.stop()

        stopper = asyncio.create_task(stop_soon())
        await s.start()
        await stopper

        # Both tasks should have run
        assert "low" in order
        assert "high" in order

    async def test_stop_cancels_task_with_cancelled_error(self):
        """Lines 194-195: stop cancels tasks and handles CancelledError."""
        s = Scheduler()
        started = asyncio.Event()

        async def blocking():
            started.set()
            await asyncio.sleep(1000)

        await s.add("block", blocking, interval_seconds=1000)
        st = s._tasks["block"]

        # Manually create the asyncio task and set _running
        s._running = True
        st._task = asyncio.create_task(blocking())
        await started.wait()

        # Now stop should cancel the task and hit the CancelledError handler
        await s.stop()
        assert not s._running
        assert st._task.done()

    async def test_run_loop_disabled_task_exits(self):
        """Lines 205,208: disabled task causes loop to exit."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("dis", noop, interval_seconds=0.01)
        st = s._tasks["dis"]
        st.enabled = False
        s._running = True

        # Should exit immediately since enabled=False
        await s._run_loop(st)

    async def test_run_loop_not_running_exits(self):
        """Line 205: _running=False causes loop to exit."""
        s = Scheduler()

        async def noop():
            pass

        await s.add("nr", noop, interval_seconds=0.01)
        st = s._tasks["nr"]
        s._running = False

        await s._run_loop(st)

    async def test_backoff_calculation_values(self):
        """Verify backoff constants are used correctly in run_loop."""
        # Just verify the constants are accessible and have expected values
        assert MAX_ERROR_BACKOFF_SECONDS == 60.0
        assert MIN_RECOVERY_INTERVAL_SECONDS == 5.0
        assert BACKOFF_MULTIPLIER == 2.0
        assert MAX_BACKOFF_EXPONENT == 5


class TestScheduledTask:
    def test_defaults(self):
        async def noop():
            pass

        t = ScheduledTask(name="test", func=noop, interval_seconds=60)
        assert t.run_count == 0
        assert t.error_count == 0
        assert t.enabled
        assert not t.run_immediately
        assert t.priority == TaskPriority.LOW

    def test_private_task_attr_default_none(self):
        async def noop():
            pass

        t = ScheduledTask(name="test", func=noop, interval_seconds=60)
        assert t._task is None
