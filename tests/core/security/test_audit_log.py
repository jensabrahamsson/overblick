"""Tests for audit log."""

import asyncio
import sqlite3
import time

import pytest

from overblick.core.security.audit_log import GENESIS_HASH, AuditLog


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

        valid, tampered = log.verify_chain()
        assert valid is True
        log.close()
