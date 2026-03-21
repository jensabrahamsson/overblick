"""Additional tests for inet_audit — cover lines 201-202, 225-234, 238-253, 258, 272-275."""

import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from overblick.gateway.inet_audit import InetAuditLog


class TestInetAuditCoverage:
    def test_query_with_since(self):
        """Cover lines 201-202: query with since filter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Insert entries directly for precise timestamps
            conn = sqlite3.connect(str(db_path))
            old_ts = time.time() - 7200
            conn.execute(
                "INSERT INTO inet_audit (timestamp, key_id, source_ip, method, path, status_code) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (old_ts, "old", "1.2.3.4", "POST", "/test", 200),
            )
            new_ts = time.time()
            conn.execute(
                "INSERT INTO inet_audit (timestamp, key_id, source_ip, method, path, status_code) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_ts, "new", "1.2.3.4", "POST", "/test", 200),
            )
            conn.commit()
            conn.close()

            since = time.time() - 3600
            entries = audit.query(since=since, limit=10)
            assert len(entries) == 1
            assert entries[0]["key_id"] == "new"
            audit.close()

    def test_count_violations(self):
        """Cover lines 225-234: count_violations method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            since = time.time() - 3600
            # Use _log_sync for synchronous writes
            audit._log_sync(
                key_id="", key_name="", source_ip="1.2.3.4",
                method="POST", path="/test", model="",
                status_code=401, request_tokens=0, response_tokens=0,
                latency_ms=0.0, error="", violation="auth_failure",
            )
            audit._log_sync(
                key_id="", key_name="", source_ip="1.2.3.4",
                method="POST", path="/test", model="",
                status_code=429, request_tokens=0, response_tokens=0,
                latency_ms=0.0, error="", violation="rate_limit",
            )
            audit._log_sync(
                key_id="", key_name="", source_ip="1.2.3.4",
                method="POST", path="/test", model="",
                status_code=200, request_tokens=0, response_tokens=0,
                latency_ms=0.0, error="", violation="",
            )

            count = audit.count_violations("1.2.3.4", since)
            assert count == 2
            audit.close()

    def test_trim_old_entries_with_deletions(self):
        """Cover lines 238-253: _trim_old_entries that actually deletes entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path, retention_days=1)

            # Insert old entry directly
            conn = sqlite3.connect(str(db_path))
            old_ts = time.time() - (2 * 86400)
            conn.execute(
                "INSERT INTO inet_audit (timestamp, key_id, source_ip, method, path, status_code) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (old_ts, "old", "1.2.3.4", "POST", "/test", 200),
            )
            conn.commit()
            conn.close()

            deleted = audit._trim_old_entries()
            assert deleted >= 1
            audit.close()

    def test_stop_background_cleanup_when_not_running(self):
        """Cover line 258: stop when no task is running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # No task was started
            audit.stop_background_cleanup()
            assert audit._cleanup_task is None
            audit.close()

    @pytest.mark.asyncio
    async def test_cleanup_loop_with_trim_exception(self):
        """Cover lines 272-275: _cleanup_loop handles _trim_old_entries exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)
            audit._CLEANUP_INTERVAL_SECONDS = 0.01

            call_count = 0

            def failing_trim():
                nonlocal call_count
                call_count += 1
                raise RuntimeError("Trim error")

            with patch.object(audit, "_trim_old_entries", side_effect=failing_trim):
                audit.start_background_cleanup()
                await asyncio.sleep(0.05)
                audit.stop_background_cleanup()

            assert call_count >= 1
            audit.close()

    def test_log_sync_outside_event_loop(self):
        """Cover lines 159-174: log() calls _log_sync when no event loop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # When called outside async context, should fall back to synchronous
            audit.log(
                key_id="sync-key",
                source_ip="1.2.3.4",
                status_code=200,
            )

            time.sleep(0.1)
            entries = audit.query(key_id="sync-key", limit=10)
            assert len(entries) >= 1
            audit.close()
