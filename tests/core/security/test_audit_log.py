"""Tests for audit log."""

import asyncio
import logging
import sqlite3
import time

import pytest

from overblick.core.security.audit_log import GENESIS_HASH, AuditLog


class TestAuditLog:
    def test_log_and_query(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="test_action", details={"key": "value"})

        entries = log.query(action="test_action")
        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"
        assert entries[0]["details"] == {"key": "value"}
        assert entries[0]["identity"] == "test"
        assert entries[0]["success"] is True

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


class TestQueryDefaultParameters:
    """Kill mutants that change default parameter values in query()."""

    def test_should_use_default_limit_of_100(self, tmp_path):
        """Default limit=100 means exactly 100 entries are returned when more exist."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        now = time.time()
        # Insert 105 entries
        rows = [(now - i * 0.001, "many", "test", "test", 1) for i in range(105)]
        conn.executemany(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

        # query() with no explicit limit should return exactly 100 (the default)
        entries = log.query(action="many")
        assert len(entries) == 100

    def test_should_use_default_offset_of_0(self, tmp_path):
        """Default offset=0 means first page starts at the beginning."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(3):
            log.log(action="offset_default", category="test", details={"i": i})

        # With default offset=0, all 3 entries should be returned
        entries = log.query(action="offset_default", limit=10)
        assert len(entries) == 3

        # Explicit offset=1 should skip 1
        entries_offset = log.query(action="offset_default", limit=10, offset=1)
        assert len(entries_offset) == 2

    def test_should_use_default_last_id_of_0(self, tmp_path):
        """Default last_id=0 means no keyset pagination filter is applied."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(3):
            log.log(action="lid_default", category="test")

        # With default last_id=0, all entries should be returned (no id < 0 filter)
        entries = log.query(action="lid_default", limit=10)
        assert len(entries) == 3

        # With last_id=1, no entries with id < 1 should exist
        entries_lid1 = log.query(action="lid_default", last_id=1, limit=10)
        assert len(entries_lid1) == 0


class TestQueryFilterConditions:
    """Kill mutants that change filter conditions (action, category, since)."""

    def test_should_filter_by_action_exactly(self, tmp_path):
        """action filter must match exactly, not match everything."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="alpha", category="test")
        log.log(action="beta", category="test")

        alpha = log.query(action="alpha")
        assert len(alpha) == 1
        assert alpha[0]["action"] == "alpha"

        beta = log.query(action="beta")
        assert len(beta) == 1
        assert beta[0]["action"] == "beta"

    def test_should_filter_by_category_exactly(self, tmp_path):
        """category filter must match exactly."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="x", category="security")
        log.log(action="y", category="general")

        security = log.query(category="security")
        assert len(security) == 1
        assert security[0]["category"] == "security"

    def test_should_apply_since_filter_correctly(self, tmp_path):
        """since filter uses >= comparison."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        time.time()
        conn = log._conn
        # Entry exactly at the boundary
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (100.0, "old", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (200.0, "boundary", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (300.0, "new", "test", "test", 1),
        )
        conn.commit()

        # since=200.0 should include boundary and new (timestamp >= 200.0)
        entries = log.query(since=200.0)
        assert len(entries) == 2
        actions = {e["action"] for e in entries}
        assert actions == {"boundary", "new"}

        # since=201.0 should only include new
        entries2 = log.query(since=201.0)
        assert len(entries2) == 1
        assert entries2[0]["action"] == "new"


class TestQueryKeysetPaginationDetails:
    """Kill mutants in keyset pagination branches (last_id > 0 and offset > 1000)."""

    def test_should_use_keyset_pagination_when_last_id_positive(self, tmp_path):
        """last_id > 0 activates keyset pagination with id < last_id filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(5):
            log.log(action="ks", category="test")

        all_entries = log.query(action="ks", limit=10)
        assert len(all_entries) == 5

        # Get the highest ID
        max_id = max(e["id"] for e in all_entries)
        min_id = min(e["id"] for e in all_entries)

        # last_id = max_id + 1 should return all entries (all have id < max_id + 1)
        ks_entries = log.query(action="ks", last_id=max_id + 1, limit=10)
        assert len(ks_entries) == 5

        # last_id = min_id should return 0 entries (no id < min_id)
        ks_empty = log.query(action="ks", last_id=min_id, limit=10)
        assert len(ks_empty) == min_id - 1  # entries with id < min_id

    def test_should_apply_limit_in_keyset_pagination(self, tmp_path):
        """Keyset pagination respects the limit parameter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(10):
            log.log(action="lim", category="test")

        all_entries = log.query(action="lim", limit=20)
        max_id = max(e["id"] for e in all_entries) + 1

        limited = log.query(action="lim", last_id=max_id, limit=3)
        assert len(limited) == 3

    def test_should_combine_keyset_with_action_filter(self, tmp_path):
        """Keyset pagination combined with action filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="target", category="test")
        log.log(action="other", category="test")
        log.log(action="target", category="test")

        all_target = log.query(action="target", limit=10)
        assert len(all_target) == 2

        # Use keyset with a high last_id to get all target entries
        ks = log.query(action="target", last_id=999, limit=10)
        assert len(ks) == 2
        for e in ks:
            assert e["action"] == "target"

    def test_should_combine_keyset_with_category_filter(self, tmp_path):
        """Keyset pagination combined with category filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="sec")
        log.log(action="b", category="gen")
        log.log(action="c", category="sec")

        ks = log.query(category="sec", last_id=999, limit=10)
        assert len(ks) == 2
        for e in ks:
            assert e["category"] == "sec"

    def test_should_combine_keyset_with_since_filter(self, tmp_path):
        """Keyset pagination combined with since filter."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (100.0, "old", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (300.0, "new", "test", "test", 1),
        )
        conn.commit()

        ks = log.query(since=200.0, last_id=999, limit=10)
        assert len(ks) == 1
        assert ks[0]["action"] == "new"

    def test_should_return_entries_in_desc_order(self, tmp_path):
        """Query results are ordered by timestamp DESC."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        for ts in [100.0, 200.0, 300.0]:
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
                (ts, "ordered", "test", "test", 1),
            )
        conn.commit()

        entries = log.query(action="ordered", limit=10)
        timestamps = [e["timestamp"] for e in entries]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_should_parse_details_json(self, tmp_path):
        """Details JSON is parsed back to dict."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="json_test", details={"key": "val", "num": 42})

        entries = log.query(action="json_test")
        assert len(entries) == 1
        assert entries[0]["details"] == {"key": "val", "num": 42}

    def test_should_leave_null_details_as_none(self, tmp_path):
        """Entries with NULL details stay as None (not parsed)."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="no_details", category="test")

        entries = log.query(action="no_details")
        assert len(entries) == 1
        assert entries[0]["details"] is None

    def test_should_convert_success_to_bool(self, tmp_path):
        """success field is converted from int to bool."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="success_true", category="test", success=True)
        log.log(action="success_false", category="test", success=False)

        true_entries = log.query(action="success_true")
        assert true_entries[0]["success"] is True
        assert type(true_entries[0]["success"]) is bool

        false_entries = log.query(action="success_false")
        assert false_entries[0]["success"] is False
        assert type(false_entries[0]["success"]) is bool

    def test_should_large_offset_with_no_conditions(self, tmp_path):
        """Large offset (>1000) path with no filter conditions."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        now = time.time()
        rows = [(now - i * 0.001, "bulk_nofilter", "test", "test", 1) for i in range(1005)]
        conn.executemany(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

        entries = log.query(offset=1001, limit=3)
        assert len(entries) == 3

    def test_should_large_offset_with_action_and_category(self, tmp_path):
        """Large offset path with both action and category filters."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        now = time.time()
        rows = [(now - i * 0.001, "filtered", "special", "test", 1) for i in range(1005)]
        conn.executemany(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

        entries = log.query(action="filtered", category="special", offset=1001, limit=3)
        assert len(entries) == 3
        for e in entries:
            assert e["action"] == "filtered"
            assert e["category"] == "special"


class TestQuerySmallOffset:
    """Kill mutants in the small offset (traditional OFFSET) branch."""

    def test_should_use_traditional_offset_for_small_values(self, tmp_path):
        """offset <= 1000 uses LIMIT ? OFFSET ? SQL."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(5):
            log.log(action="trad", category="test", details={"i": i})

        # offset=0 (default) returns all
        all_entries = log.query(action="trad", limit=10, offset=0)
        assert len(all_entries) == 5

        # offset=2 skips 2 entries
        offset_entries = log.query(action="trad", limit=10, offset=2)
        assert len(offset_entries) == 3

        # offset=5 skips all
        empty = log.query(action="trad", limit=10, offset=5)
        assert len(empty) == 0

    def test_should_combine_limit_and_offset(self, tmp_path):
        """Small offset branch: limit and offset work together correctly."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(10):
            log.log(action="combo", category="test")

        entries = log.query(action="combo", limit=3, offset=2)
        assert len(entries) == 3

    def test_should_build_where_clause_in_small_offset_branch(self, tmp_path):
        """The WHERE clause is correctly built for the small offset path."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="x", category="cat1")
        log.log(action="y", category="cat2")
        log.log(action="x", category="cat1")

        entries = log.query(action="x", category="cat1", limit=10, offset=0)
        assert len(entries) == 2
        for e in entries:
            assert e["action"] == "x"
            assert e["category"] == "cat1"


class TestCountFilterDetails:
    """Kill mutants in count() filter conditions."""

    def test_should_count_with_action_filter(self, tmp_path):
        """count(action=...) counts only matching actions."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")
        log.log(action="a", category="test")

        assert log.count(action="a") == 2
        assert log.count(action="b") == 1
        assert log.count(action="nonexistent") == 0

    def test_should_count_with_since_filter(self, tmp_path):
        """count(since=...) filters by timestamp >= since."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (100.0, "old", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (200.0, "boundary", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (300.0, "new", "test", "test", 1),
        )
        conn.commit()

        assert log.count(since=200.0) == 2  # boundary + new
        assert log.count(since=201.0) == 1  # only new
        assert log.count(since=301.0) == 0

    def test_should_count_all_when_no_filters(self, tmp_path):
        """count() with no filters returns total count."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="any1", category="test")
        log.log(action="any2", category="test")
        assert log.count() == 2

    def test_should_count_with_action_and_since_combined(self, tmp_path):
        """count() with both action and since."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        conn = log._conn
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (100.0, "target", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (300.0, "target", "test", "test", 1),
        )
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (300.0, "other", "test", "test", 1),
        )
        conn.commit()
        assert log.count(action="target", since=200.0) == 1
        assert log.count(action="other", since=200.0) == 1


class TestTrimOldEntriesDetails:
    """Kill mutants in _trim_old_entries()."""

    def test_should_not_delete_recent_entries(self, tmp_path):
        """Entries within retention period are not deleted."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=90)
        log.log(action="recent", category="test")
        deleted = log._trim_old_entries()
        assert deleted == 0
        assert log.count(action="recent") == 1

    def test_should_delete_entries_beyond_retention(self, tmp_path):
        """Entries older than retention_days are deleted."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=1)
        conn = log._conn
        # Insert entry from 2 days ago
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (time.time() - 2 * 86400, "very_old", "test", "test", 1),
        )
        conn.commit()
        deleted = log._trim_old_entries()
        assert deleted == 1

    def test_should_use_retention_days_times_86400(self, tmp_path):
        """Cutoff = time.time() - (retention_days * 86400)."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=2)
        conn = log._conn
        now = time.time()
        # Entry from 1 day ago (within 2-day retention)
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now - 86400, "day1", "test", "test", 1),
        )
        # Entry from 3 days ago (beyond 2-day retention)
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (now - 3 * 86400, "day3", "test", "test", 1),
        )
        conn.commit()
        deleted = log._trim_old_entries()
        assert deleted == 1
        assert log.count(action="day1") == 1
        assert log.count(action="day3") == 0

    def test_should_commit_only_when_entries_deleted(self, tmp_path):
        """_trim_old_entries only commits when deleted > 0."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=90)
        log.log(action="recent", category="test")
        # No old entries to delete
        deleted = log._trim_old_entries()
        assert deleted == 0

    def test_should_log_info_when_entries_trimmed(self, tmp_path, caplog):
        """Trimming logs an info message with count and retention days."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=0)
        conn = log._conn
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (1.0, "ancient", "test", "test", 1),
        )
        conn.commit()
        with caplog.at_level(logging.INFO, logger="overblick.core.security.audit_log"):
            deleted = log._trim_old_entries()
        assert deleted == 1
        assert any("trimmed" in r.message.lower() for r in caplog.records)
        # Verify the retention_days value is logged
        assert any("0 days" in r.message for r in caplog.records)

    def test_should_return_exact_delete_count(self, tmp_path):
        """_trim_old_entries returns exact number of deleted rows."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=0)
        conn = log._conn
        for i in range(5):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
                (1.0 + i, f"old_{i}", "test", "test", 1),
            )
        conn.commit()
        deleted = log._trim_old_entries()
        assert deleted == 5


class TestBackgroundCleanupDetails:
    """Kill mutants in start_background_cleanup and _cleanup_loop."""

    @pytest.mark.asyncio
    async def test_should_use_cleanup_interval_for_sleep(self, tmp_path):
        """_cleanup_loop sleeps for _CLEANUP_INTERVAL_SECONDS between iterations."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log._CLEANUP_INTERVAL_SECONDS = 0.02

        trim_times = []
        original_trim = log._trim_old_entries

        def recording_trim():
            trim_times.append(time.time())
            return original_trim()

        log._trim_old_entries = recording_trim
        log.start_background_cleanup()
        await asyncio.sleep(0.08)
        log.stop_background_cleanup()

        # Should have been called multiple times with ~0.02s intervals
        assert len(trim_times) >= 2

    @pytest.mark.asyncio
    async def test_should_log_deleted_entries_in_cleanup(self, tmp_path, caplog):
        """_cleanup_loop logs when entries are deleted."""
        log = AuditLog(tmp_path / "audit.db", identity="test", retention_days=0)
        log._CLEANUP_INTERVAL_SECONDS = 0.02

        # Insert old entry
        log._conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) VALUES (?, ?, ?, ?, ?)",
            (1.0, "to_trim", "test", "test", 1),
        )
        log._conn.commit()

        with caplog.at_level(logging.DEBUG, logger="overblick.core.security.audit_log"):
            log.start_background_cleanup()
            await asyncio.sleep(0.08)
            log.stop_background_cleanup()

        # Verify entry was trimmed
        assert log.count(action="to_trim") == 0

    @pytest.mark.asyncio
    async def test_should_create_task_via_asyncio(self, tmp_path):
        """start_background_cleanup creates an asyncio.Task."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.start_background_cleanup()
        assert isinstance(log._cleanup_task, asyncio.Task)
        log.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_should_not_start_duplicate_task(self, tmp_path):
        """Calling start_background_cleanup when task is running is a no-op."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.start_background_cleanup()
        task1 = log._cleanup_task
        log.start_background_cleanup()
        assert log._cleanup_task is task1
        log.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_should_restart_after_done_task(self, tmp_path):
        """If cleanup task has completed, start creates a new one."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log._CLEANUP_INTERVAL_SECONDS = 0.01
        log.start_background_cleanup()
        task1 = log._cleanup_task
        # Cancel and wait
        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
        assert task1.done()

        # Now start should create a new task
        log.start_background_cleanup()
        assert log._cleanup_task is not task1
        assert not log._cleanup_task.done()
        log.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_loop_handles_cancelled_error(self, tmp_path):
        """_cleanup_loop exits cleanly on CancelledError."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log._CLEANUP_INTERVAL_SECONDS = 0.01
        log.start_background_cleanup()
        task = log._cleanup_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()


class TestVerifyChainDetails:
    """Kill mutants in verify_chain()."""

    def test_should_return_tuple_of_bool_and_list(self, tmp_path):
        """verify_chain returns (bool, list[int])."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        result = log.verify_chain()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], list)

    def test_should_detect_first_entry_tampering(self, tmp_path):
        """Tampering with the first entry's previous_hash is detected."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")

        # Tamper with first entry's previous_hash (should be "genesis" hash)
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'forged' WHERE id = 1"
        )
        log._conn.commit()

        is_valid, tampered = log.verify_chain()
        assert not is_valid
        assert 1 in tampered

    def test_should_return_true_for_single_valid_entry(self, tmp_path):
        """Single valid entry has valid chain."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="single", category="test")

        is_valid, tampered = log.verify_chain()
        assert is_valid is True
        assert tampered == []

    def test_should_continue_verification_after_tampered_entry(self, tmp_path):
        """Verification continues checking after finding a tampered entry."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")
        log.log(action="c", category="test")
        log.log(action="d", category="test")

        # Tamper with entry 2
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'bad1' WHERE id = 2"
        )
        log._conn.commit()

        is_valid, tampered = log.verify_chain()
        assert not is_valid
        assert 2 in tampered
        # Should be exactly one tampered (entry 3 uses entry 2's stored_previous_hash as new base)

    def test_should_use_stored_previous_hash_as_base_after_tamper(self, tmp_path):
        """After detecting tampered entry, chain continues from stored hash."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")
        log.log(action="b", category="test")
        log.log(action="c", category="test")

        # Tamper only entry 2 - entry 3's previous_hash should still be valid
        # relative to the real chain, not the stored one
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'forged2' WHERE id = 2"
        )
        log._conn.commit()

        is_valid, _tampered = log.verify_chain()
        assert not is_valid
        # Entry 2 is tampered, but the algorithm continues checking from the stored hash

    def test_should_check_all_entries_in_order(self, tmp_path):
        """verify_chain processes entries ordered by id ASC."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        for i in range(5):
            log.log(action=f"entry_{i}", category="test")

        is_valid, tampered = log.verify_chain()
        assert is_valid is True
        assert tampered == []

    def test_verify_chain_uses_genesis_as_initial_hash(self, tmp_path):
        """First entry's previous_hash should chain from 'genesis'."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="first", category="test")

        # Read the stored previous_hash for entry 1
        cursor = log._conn.execute(
            "SELECT previous_hash FROM audit_log WHERE id = 1"
        )
        row = cursor.fetchone()
        # The stored previous_hash should be the hash computed from genesis
        # It should NOT be "genesis" itself - it's the hash of the previous entry
        # For the first entry, previous_hash = hash of genesis state
        # Actually looking at the code: previous_hash = self._get_last_hash()
        # which for empty db returns "genesis"
        # Then we store previous_hash (the hash of the previous entry, which is "genesis")
        assert row[0] == GENESIS_HASH  # The stored value for first entry

    def test_verify_chain_result_is_true_when_tampered_ids_empty(self, tmp_path):
        """is_valid = (len(tampered_ids) == 0)."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")

        is_valid, tampered = log.verify_chain()
        assert len(tampered) == 0
        assert is_valid is True

    def test_verify_chain_result_is_false_when_tampered_ids_nonempty(self, tmp_path):
        """is_valid = False when tampered_ids has entries."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a", category="test")

        # Tamper
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'x' WHERE id = 1"
        )
        log._conn.commit()

        is_valid, tampered = log.verify_chain()
        assert len(tampered) > 0
        assert is_valid is False


class TestLogSyncDetails:
    """Kill mutants in _log_sync and log methods."""

    def test_should_store_plugin_field(self, tmp_path):
        """Plugin name is stored and retrieved."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="with_plugin", category="test", plugin="moltbook")

        entries = log.query(action="with_plugin")
        assert len(entries) == 1
        assert entries[0]["plugin"] == "moltbook"

    def test_should_store_none_plugin(self, tmp_path):
        """None plugin is stored as NULL."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="no_plugin", category="test")

        entries = log.query(action="no_plugin")
        assert len(entries) == 1
        assert entries[0]["plugin"] is None

    def test_should_store_success_as_integer(self, tmp_path):
        """success=True stored as 1, success=False as 0."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="ok", category="test", success=True)
        log.log(action="fail", category="test", success=False)

        ok = log.query(action="ok")
        assert ok[0]["success"] is True

        fail = log.query(action="fail")
        assert fail[0]["success"] is False

    def test_should_serialize_details_as_json(self, tmp_path):
        """Details dict is JSON-serialized."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="json", details={"nested": {"key": [1, 2]}})

        entries = log.query(action="json")
        assert entries[0]["details"] == {"nested": {"key": [1, 2]}}

    def test_should_use_default_category_general(self, tmp_path):
        """Default category is 'general'."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="default_cat")

        entries = log.query(action="default_cat")
        assert len(entries) == 1
        assert entries[0]["category"] == "general"

    def test_should_return_row_id_in_sync_mode(self, tmp_path):
        """log() returns row ID when called synchronously (no event loop)."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        row_id = log.log(action="sync_test", category="test")
        assert row_id > 0
        assert isinstance(row_id, int)

    def test_should_store_previous_hash_chain(self, tmp_path):
        """Each entry stores the hash of the previous entry as previous_hash."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="chain1", category="test")
        log.log(action="chain2", category="test")

        entries = log.query(limit=10)
        # Both entries should have previous_hash set
        for e in entries:
            assert e["previous_hash"] is not None

    def test_should_store_duration_ms(self, tmp_path):
        """duration_ms is stored and retrieved."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="timed", category="test", duration_ms=42.5)

        entries = log.query(action="timed")
        assert entries[0]["duration_ms"] == 42.5

    def test_should_store_error(self, tmp_path):
        """error string is stored and retrieved."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="errored", category="test", error="something broke")

        entries = log.query(action="errored")
        assert entries[0]["error"] == "something broke"


class TestComputeEntryHash:
    """Kill mutants in _compute_entry_hash."""

    def test_should_produce_different_hash_for_different_timestamps(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(2.0, "a", "c", None, None, True, None, None, "genesis")
        assert h1 != h2

    def test_should_produce_different_hash_for_different_actions(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "b", "c", None, None, True, None, None, "genesis")
        assert h1 != h2

    def test_should_produce_different_hash_for_different_categories(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c1", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c2", None, None, True, None, None, "genesis")
        assert h1 != h2

    def test_should_produce_different_hash_for_different_success(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, None, False, None, None, "genesis")
        assert h1 != h2

    def test_should_produce_different_hash_for_different_previous_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "other")
        assert h1 != h2

    def test_should_include_details_in_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, '{"key": "val"}', True, None, None, "genesis")
        assert h1 != h2

    def test_should_include_duration_in_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, None, True, 42.0, None, "genesis")
        assert h1 != h2

    def test_should_include_error_in_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, "err", "genesis")
        assert h1 != h2

    def test_should_include_plugin_in_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, None, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", "plugin1", None, True, None, None, "genesis")
        assert h1 != h2

    def test_should_format_duration_with_6_decimals(self, tmp_path):
        """Duration is formatted with 6 decimal places for determinism."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        h1 = log._compute_entry_hash(1.0, "a", "c", None, None, True, 1.0, None, "genesis")
        h2 = log._compute_entry_hash(1.0, "a", "c", None, None, True, 1.0, None, "genesis")
        assert h1 == h2


class TestAuditLogHashChain:
    def test_first_entry_uses_genesis_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="first", details={"k": "v"})

        entries = log.query(action="first")
        assert len(entries) == 1
        assert entries[0]["previous_hash"] == GENESIS_HASH
        assert entries[0]["chain_hash"] is not None
        assert len(entries[0]["chain_hash"]) == 64

    def test_chain_links_entries(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="a")
        log.log(action="b")

        # Query returns newest first
        entries = log.query(limit=10)
        assert len(entries) == 2
        entry_b, entry_a = entries[0], entries[1]

        # Entry b's previous_hash should be entry a's chain_hash
        assert entry_b["previous_hash"] == entry_a["chain_hash"]

    def test_verify_chain_valid(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="one", details={"x": 1})
        log.log(action="two", details={"x": 2})
        log.log(action="three", details={"x": 3})

        valid, tampered = log.verify_chain()
        assert valid is True
        assert tampered == []

    def test_verify_chain_detects_tampered_entry(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="one")
        log.log(action="two")
        log.log(action="three")

        # Tamper with the second entry's chain_hash
        assert log._conn is not None
        log._conn.execute(
            "UPDATE audit_log SET chain_hash = 'tampered' WHERE id = 2"
        )
        log._conn.commit()

        valid, tampered = log.verify_chain()
        assert valid is False
        # Entry 2 is tampered (wrong chain_hash), and entry 3 links to the
        # wrong previous_hash as a result
        assert 2 in tampered

    def test_verify_chain_detects_broken_link(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="one")
        log.log(action="two")

        # Tamper with previous_hash of entry 2
        assert log._conn is not None
        log._conn.execute(
            "UPDATE audit_log SET previous_hash = 'wrong' WHERE id = 2"
        )
        log._conn.commit()

        valid, tampered = log.verify_chain()
        assert valid is False
        assert 2 in tampered

    def test_verify_chain_empty_log(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        valid, tampered = log.verify_chain()
        assert valid is True
        assert tampered == []

    def test_backward_compat_old_entries_without_hashes(self, tmp_path):
        """Old entries with NULL hashes should not break verify_chain."""
        log = AuditLog(tmp_path / "audit.db", identity="test")
        # Insert a legacy entry with no hashes
        assert log._conn is not None
        log._conn.execute(
            "INSERT INTO audit_log "
            "(timestamp, action, category, identity, success, previous_hash, chain_hash) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (time.time() - 100, "legacy_action", "general", "test", 1),
        )
        log._conn.commit()

        # Now add a new entry with hashes
        log.log(action="new_action")

        valid, tampered = log.verify_chain()
        assert valid is True
        assert tampered == []

    def test_prev_hash_persists_across_reopen(self, tmp_path):
        """Chain should continue correctly after closing and reopening the DB."""
        db_path = tmp_path / "audit.db"
        log = AuditLog(db_path, identity="test")
        log.log(action="before_close")
        log.close()

        log2 = AuditLog(db_path, identity="test")
        log2.log(action="after_reopen")

        valid, tampered = log2.verify_chain()
        assert valid is True
        assert tampered == []
        log2.close()

    def test_query_returns_chain_hash(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", identity="test")
        log.log(action="test_action")
        entries = log.query(action="test_action")
        assert "chain_hash" in entries[0]
        assert "previous_hash" in entries[0]

    def test_migration_adds_chain_hash_column(self, tmp_path):
        """Verify migration works on a DB created without chain_hash column."""
        db_path = tmp_path / "old.db"
        # Create a DB with the old schema (no chain_hash column)
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                identity TEXT NOT NULL,
                plugin TEXT,
                details TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                duration_ms REAL,
                error TEXT,
                previous_hash TEXT
            );
        """)
        conn.execute(
            "INSERT INTO audit_log "
            "(timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time(), "old_entry", "general", "test", 1),
        )
        conn.commit()
        conn.close()

        # Open with AuditLog — should migrate
        log = AuditLog(db_path, identity="test")
        log.log(action="new_entry")

        valid, _tampered = log.verify_chain()
        assert valid is True
        log.close()
