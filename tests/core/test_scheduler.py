"""Tests for scheduler."""

import asyncio

import pytest

from overblick.core.scheduler import ScheduledTask, Scheduler, TaskPriority


class TestScheduler:
    @pytest.mark.asyncio
    async def test_add_task(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        assert s.task_count == 1

    @pytest.mark.asyncio
    async def test_add_duplicate_raises(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        with pytest.raises(ValueError, match="already registered"):
            await s.add("task1", noop, interval_seconds=60)

    @pytest.mark.asyncio
    async def test_remove_task(self):
        s = Scheduler()

        async def noop():
            pass

        await s.add("task1", noop, interval_seconds=60)
        assert await s.remove("task1")
        assert s.task_count == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        s = Scheduler()
        assert not await s.remove("nope")

    @pytest.mark.asyncio
    async def test_get_stats_empty(self):
        s = Scheduler()
        stats = await s.get_stats()
        assert stats == {}

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_execute_error_increments_error_count(self):
        s = Scheduler()

        async def fail():
            raise RuntimeError("boom")

        await s.add("fail", fail, interval_seconds=999)
        task = s._tasks["fail"]
        await s._execute(task)

        assert task.error_count == 1
        assert task.run_count == 0

    @pytest.mark.asyncio
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
