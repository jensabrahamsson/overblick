"""Tests for audit log."""

import asyncio
import time

import pytest
from overblick.core.security.audit_log import AuditLog


class TestAuditLog:
    def test_log_and_query(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="test_action", details={"key": "value"})

        entries = log.query(action="test_action")
        assert len(entries) >= 1
        assert entries[0]["action"] == "test_action"

    def test_log_multiple(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", details={})
        log.log(action="b", details={})
        log.log(action="a", details={"x": 1})

        a_entries = log.query(action="a")
        assert len(a_entries) == 2

        b_entries = log.query(action="b")
        assert len(b_entries) == 1

    def test_count(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="counted", details={})
        log.log(action="counted", details={})
        log.log(action="other", details={})

        assert log.count(action="counted") == 2
        assert log.count(action="other") == 1

    def test_empty_query(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        assert log.query(action="nonexistent") == []

    def test_close_stops_background_cleanup(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.close()
        assert log._conn is None

    def test_trim_removes_old_entries(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=0)
        # Insert entries with old timestamps
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time() - 86400, "old_action", "test", "test", 1),
        )
        log._conn.commit()
        assert log.count(action="old_action") == 1

        deleted = log._trim_old_entries()
        assert deleted == 1
        assert log.count(action="old_action") == 0


class TestAuditLogBackgroundCleanup:
    @pytest.mark.asyncio
    async def test_start_background_cleanup_creates_task(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        assert log._cleanup_task is None

        log.start_background_cleanup()
        assert log._cleanup_task is not None
        assert not log._cleanup_task.done()

        log.stop_background_cleanup()
        assert log._cleanup_task is None

    @pytest.mark.asyncio
    async def test_start_cleanup_idempotent(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.start_background_cleanup()
        task1 = log._cleanup_task

        log.start_background_cleanup()
        task2 = log._cleanup_task

        assert task1 is task2
        log.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_stop_cleanup_when_not_started(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # Should not raise
        log.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_close_cancels_background_task(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.start_background_cleanup()
        task = log._cleanup_task
        log.close()
        # Give event loop a tick to process the cancellation
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_cleanup_loop_runs_trim(self, tmp_path):
        """Verify the cleanup loop actually calls _trim_old_entries."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # Set a very short interval for testing
        log._CLEANUP_INTERVAL_SECONDS = 0.05

        # Insert an old entry
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (1.0, "ancient", "test", "test", 1),
        )
        log._conn.commit()
        assert log.count(action="ancient") == 1

        log.start_background_cleanup()
        await asyncio.sleep(0.15)
        log.stop_background_cleanup()

        assert log.count(action="ancient") == 0
