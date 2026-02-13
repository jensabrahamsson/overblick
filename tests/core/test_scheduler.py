"""Tests for scheduler."""

import asyncio
import pytest
from overblick.core.scheduler import Scheduler, ScheduledTask


class TestScheduler:
    def test_add_task(self):
        s = Scheduler()

        async def noop():
            pass

        s.add("task1", noop, interval_seconds=60)
        assert s.task_count == 1

    def test_add_duplicate_raises(self):
        s = Scheduler()

        async def noop():
            pass

        s.add("task1", noop, interval_seconds=60)
        with pytest.raises(ValueError, match="already registered"):
            s.add("task1", noop, interval_seconds=60)

    def test_remove_task(self):
        s = Scheduler()

        async def noop():
            pass

        s.add("task1", noop, interval_seconds=60)
        assert s.remove("task1")
        assert s.task_count == 0

    def test_remove_nonexistent(self):
        s = Scheduler()
        assert not s.remove("nope")

    def test_get_stats_empty(self):
        s = Scheduler()
        assert s.get_stats() == {}

    def test_get_stats_with_tasks(self):
        s = Scheduler()

        async def noop():
            pass

        s.add("t1", noop, interval_seconds=30)
        stats = s.get_stats()
        assert "t1" in stats
        assert stats["t1"]["interval_seconds"] == 30
        assert stats["t1"]["run_count"] == 0

    @pytest.mark.asyncio
    async def test_execute_increments_count(self):
        s = Scheduler()
        counter = {"n": 0}

        async def inc():
            counter["n"] += 1

        s.add("inc", inc, interval_seconds=999)
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

        s.add("fail", fail, interval_seconds=999)
        task = s._tasks["fail"]
        await s._execute(task)

        assert task.error_count == 1
        assert task.run_count == 0


class TestScheduledTask:
    def test_defaults(self):
        async def noop():
            pass

        t = ScheduledTask(name="test", func=noop, interval_seconds=60)
        assert t.run_count == 0
        assert t.error_count == 0
        assert t.enabled
        assert not t.run_immediately
