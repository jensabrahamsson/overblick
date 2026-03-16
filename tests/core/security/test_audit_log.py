"""Tests for audit log."""

import asyncio
import json
import time
from unittest.mock import patch

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


class TestHashChain:
    def test_should_compute_entry_hash_with_duration_and_error(self, tmp_path):
        """Hash computation includes duration_ms and error fields."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(
            1.0, "action", "cat", None, None, True, 42.123456, "some error", "genesis"
        )
        h2 = log._compute_entry_hash(
            1.0, "action", "cat", None, None, True, 42.123456, "some error", "genesis"
        )
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_should_log_hash_chain_mismatch(self, tmp_path, caplog):
        """Hash chain mismatch is logged when _get_last_hash returns unexpected value."""
        import logging

        log = AuditLog(tmp_path / "audit.db", identity="test")

        # We need _get_last_hash to return a different value after the INSERT
        # than what _compute_entry_hash computed before. We can do this by
        # making the second call to _get_last_hash return a different value.
        original_get_last_hash = log._get_last_hash
        call_count = 0

        def patched_get_last_hash():
            nonlocal call_count
            call_count += 1
            # First call: normal (used to compute previous_hash for new entry)
            # Second call: return wrong hash (simulates concurrent tampering)
            if call_count % 2 == 0:
                return "tampered_hash"
            return original_get_last_hash()

        log._get_last_hash = patched_get_last_hash

        with caplog.at_level(logging.ERROR, logger="overblick.core.security.audit_log"):
            log.log(action="first", category="test")

        assert any("hash chain mismatch" in r.message.lower() for r in caplog.records)

    def test_should_verify_chain_integrity_valid(self, tmp_path):
        """verify_chain returns True for untampered log."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")
        log.log(action="c", category="test")

        is_valid, tampered = log.verify_chain()
        assert is_valid
        assert tampered == []

    def test_should_verify_chain_integrity_empty(self, tmp_path):
        """verify_chain returns True for empty log."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        is_valid, tampered = log.verify_chain()
        assert is_valid
        assert tampered == []

    def test_should_detect_tampered_chain(self, tmp_path):
        """verify_chain detects tampered entries."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")
        log.log(action="c", category="test")

        # Tamper with middle entry's previous_hash
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'forged' WHERE id = 2"
        )
        log._conn.commit()

        is_valid, tampered = log.verify_chain()
        assert not is_valid
        assert 2 in tampered


class TestAsyncLogPath:
    @pytest.mark.asyncio
    async def test_should_offload_to_executor_when_event_loop_running(self, tmp_path):
        """log() offloads write to executor when event loop is running."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # We're inside an async context, so log() should use executor
        row_id = log.log(action="async_action", category="test")
        assert row_id == 0  # Async path returns 0

        # Wait for executor to finish
        log._write_executor.shutdown(wait=True)
        # Recreate executor for close()
        from concurrent.futures import ThreadPoolExecutor
        log._write_executor = ThreadPoolExecutor(max_workers=1)

        entries = log.query(action="async_action")
        assert len(entries) == 1
        log.close()


class TestLogWithFailureFields:
    def test_should_log_with_error_and_duration(self, tmp_path):
        """Log entry with error and duration_ms fields."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(
            action="failed_op",
            category="test",
            success=False,
            duration_ms=123.456,
            error="something broke",
        )
        entries = log.query(action="failed_op")
        assert len(entries) == 1
        assert entries[0]["success"] is False
        assert entries[0]["duration_ms"] == 123.456
        assert entries[0]["error"] == "something broke"


class TestQueryPagination:
    def test_should_paginate_with_last_id(self, tmp_path):
        """Keyset pagination with last_id returns correct entries."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(5):
            log.log(action="paginated", category="test", details={"i": i})

        # Get all entries to find IDs
        all_entries = log.query(action="paginated", limit=5)
        assert len(all_entries) == 5

        # Use last_id to paginate
        mid_id = all_entries[2]["id"]
        page = log.query(action="paginated", last_id=mid_id, limit=10)
        assert all(e["id"] < mid_id for e in page)

    def test_should_paginate_with_large_offset(self, tmp_path):
        """Large offset (>1000) uses keyset pagination via subquery."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # Insert enough entries. We need > 1000 entries to trigger large offset path.
        # But that's slow. Instead, we can use offset=1001 with few entries => empty result
        for i in range(5):
            log.log(action="offset_test", category="test")

        # offset > 1000 with action filter
        entries = log.query(action="offset_test", offset=1001, limit=10)
        assert entries == []

    def test_should_paginate_with_large_offset_returning_results(self, tmp_path):
        """Large offset keyset pagination returns results when data exists beyond offset."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # We need at least 1002 entries to test the success path
        conn = log._conn
        now = time.time()
        rows = [
            (now - i * 0.001, "bulk", "test", "test", 1) for i in range(1005)
        ]
        conn.executemany(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

        entries = log.query(action="bulk", offset=1001, limit=3)
        assert len(entries) == 3

    def test_should_query_with_since_filter(self, tmp_path):
        """Query with since filter returns only recent entries."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        now = time.time()
        # Insert old entry
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now - 3600, "old", "test", "test", 1),
        )
        # Insert recent entry
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now, "recent", "test", "test", 1),
        )
        log._conn.commit()

        entries = log.query(since=now - 100)
        assert len(entries) == 1
        assert entries[0]["action"] == "recent"

    def test_should_handle_invalid_json_in_details(self, tmp_path):
        """Entries with invalid JSON in details are returned as-is."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success, details) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), "bad_json", "test", "test", 1, "not valid json {{{"),
        )
        log._conn.commit()

        entries = log.query(action="bad_json")
        assert len(entries) == 1
        assert entries[0]["details"] == "not valid json {{{"

    def test_should_query_with_category_filter(self, tmp_path):
        """Query with category filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="security")
        log.log(action="b", category="general")

        entries = log.query(category="security")
        assert len(entries) == 1
        assert entries[0]["action"] == "a"

    def test_should_query_with_keyset_and_since(self, tmp_path):
        """Query with both last_id and since filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        now = time.time()
        log.log(action="x", category="test")
        log.log(action="y", category="test")

        all_entries = log.query(limit=10)
        if len(all_entries) >= 2:
            entries = log.query(last_id=all_entries[0]["id"] + 1, since=now - 100)
            assert len(entries) >= 1


class TestCountWithSince:
    def test_should_count_with_since_filter(self, tmp_path):
        """count() with since parameter filters by timestamp."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        now = time.time()
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now - 7200, "old_counted", "test", "test", 1),
        )
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now, "new_counted", "test", "test", 1),
        )
        log._conn.commit()

        assert log.count(since=now - 100) == 1
        assert log.count(since=now - 8000) == 2

    def test_should_count_with_action_and_since(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        now = time.time()
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now, "target", "test", "test", 1),
        )
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now - 7200, "target", "test", "test", 1),
        )
        log._conn.commit()
        assert log.count(action="target", since=now - 100) == 1


class TestCleanupLoopExceptionHandling:
    @pytest.mark.asyncio
    async def test_should_survive_cleanup_exception(self, tmp_path):
        """Cleanup loop continues even if _trim_old_entries raises."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log._CLEANUP_INTERVAL_SECONDS = 0.02

        call_count = 0
        original_trim = log._trim_old_entries

        def failing_trim():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB error")
            return original_trim()

        log._trim_old_entries = failing_trim
        log.start_background_cleanup()
        await asyncio.sleep(0.1)
        log.stop_background_cleanup()

        # Should have been called multiple times despite first failure
        assert call_count >= 2
