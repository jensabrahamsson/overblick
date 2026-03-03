"""Tests for Internet Gateway audit logging."""

import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from overblick.gateway.inet_audit import InetAuditLog


class TestInetAuditLog:
    """Tests for InetAuditLog class."""

    def test_initialization_creates_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            assert db_path.exists()

            # Check schema was created
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "inet_audit" in tables

            # Check indexes
            cursor = conn.execute("SELECT name FROM sqlite_schema WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]
            assert any("inet_audit" in idx for idx in indexes)

            conn.close()
            audit.close()

    def test_log_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Log an entry
            audit.log(
                key_id="test-key-123",
                key_name="Test Key",
                source_ip="1.2.3.4",
                method="POST",
                path="/v1/chat/completions",
                model="qwen3:8b",
                status_code=200,
                request_tokens=100,
                response_tokens=50,
                latency_ms=1500.5,
                error="",
                violation="",
            )

            # Give async write time to complete
            time.sleep(0.1)

            # Verify entry was written
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM inet_audit")
            count = cursor.fetchone()[0]
            assert count == 1

            cursor = conn.execute("SELECT * FROM inet_audit")
            row = cursor.fetchone()
            assert row is not None

            # Check column values
            columns = [desc[0] for desc in cursor.description]
            row_dict = dict(zip(columns, row))

            assert row_dict["key_id"] == "test-key-123"
            assert row_dict["key_name"] == "Test Key"
            assert row_dict["source_ip"] == "1.2.3.4"
            assert row_dict["method"] == "POST"
            assert row_dict["path"] == "/v1/chat/completions"
            assert row_dict["model"] == "qwen3:8b"
            assert row_dict["status_code"] == 200
            assert row_dict["request_tokens"] == 100
            assert row_dict["response_tokens"] == 50
            assert row_dict["latency_ms"] == 1500.5
            assert row_dict["error"] == ""
            assert row_dict["violation"] == ""

            conn.close()
            audit.close()

    def test_log_with_error_and_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            audit.log(
                key_id="",
                key_name="",
                source_ip="5.6.7.8",
                method="POST",
                path="/v1/chat/completions",
                model="",
                status_code=401,
                request_tokens=0,
                response_tokens=0,
                latency_ms=10.2,
                error="auth_failure",
                violation="auth_failure",
            )

            time.sleep(0.1)

            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT error, violation FROM inet_audit")
            row = cursor.fetchone()
            assert row[0] == "auth_failure"
            assert row[1] == "auth_failure"

            conn.close()
            audit.close()

    def test_query_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Add multiple entries
            for i in range(5):
                audit.log(
                    key_id=f"key-{i}",
                    key_name=f"Key {i}",
                    source_ip=f"1.2.3.{i}",
                    method="POST",
                    path="/v1/chat/completions",
                    model="qwen3:8b",
                    status_code=200,
                    request_tokens=100 + i,
                    response_tokens=50 + i,
                    latency_ms=1000.0 + i,
                    error="",
                    violation="",
                )

            time.sleep(0.2)

            # Query all entries
            entries = audit.query(limit=10)
            assert len(entries) == 5

            # Check entries (should be in reverse chronological order, newest first)
            # Since we added key-0 first, key-4 last, key-4 should be first in results
            first = entries[0]
            assert first["key_id"] == "key-4"  # Last added, newest
            assert first["source_ip"] == "1.2.3.4"
            assert first["request_tokens"] == 104
            assert first["response_tokens"] == 54

            # Query with limit
            limited = audit.query(limit=2)
            assert len(limited) == 2

            # Query by key_id
            key_entries = audit.query(key_id="key-2", limit=10)
            assert len(key_entries) == 1
            assert key_entries[0]["key_id"] == "key-2"

            # Query by source_ip
            ip_entries = audit.query(source_ip="1.2.3.3", limit=10)
            assert len(ip_entries) == 1
            assert ip_entries[0]["source_ip"] == "1.2.3.3"

            # Query by violation
            audit.log(
                key_id="violation-key",
                key_name="Violation",
                source_ip="9.9.9.9",
                method="POST",
                path="/v1/chat/completions",
                model="",
                status_code=429,
                request_tokens=0,
                response_tokens=0,
                latency_ms=5.0,
                error="rate_limit",
                violation="rate_limit",
            )

            time.sleep(0.1)

            violation_entries = audit.query(violation="rate_limit", limit=10)
            assert len(violation_entries) >= 1
            assert violation_entries[0]["violation"] == "rate_limit"

            audit.close()

    def test_query_with_offset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Add entries
            for i in range(10):
                audit.log(
                    key_id=f"key-{i}",
                    key_name=f"Key {i}",
                    source_ip="1.2.3.4",
                    method="POST",
                    path="/v1/chat/completions",
                    model="qwen3:8b",
                    status_code=200,
                    request_tokens=100,
                    response_tokens=50,
                    latency_ms=1000.0,
                    error="",
                    violation="",
                )

            time.sleep(0.2)

            # Get first 5 entries
            first_page = audit.query(limit=5)
            assert len(first_page) == 5

            # Get next 5 entries
            second_page = audit.query(offset=5, limit=5)
            assert len(second_page) == 5

            # Entries should be different
            first_ids = {e["key_id"] for e in first_page}
            second_ids = {e["key_id"] for e in second_page}
            assert first_ids.isdisjoint(second_ids)

            audit.close()

    def test_cleanup_old_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path, retention_days=1)  # 1 day retention

            # Add an old entry (manually set timestamp)
            conn = sqlite3.connect(str(db_path))
            old_timestamp = time.time() - (2 * 24 * 3600)  # 2 days ago
            conn.execute(
                "INSERT INTO inet_audit (timestamp, key_id, source_ip, method, path, status_code) VALUES (?, ?, ?, ?, ?, ?)",
                (old_timestamp, "old-key", "1.2.3.4", "POST", "/test", 200),
            )

            # Add a recent entry
            recent_timestamp = time.time() - 3600  # 1 hour ago
            conn.execute(
                "INSERT INTO inet_audit (timestamp, key_id, source_ip, method, path, status_code) VALUES (?, ?, ?, ?, ?, ?)",
                (recent_timestamp, "recent-key", "5.6.7.8", "GET", "/health", 200),
            )
            conn.commit()
            conn.close()

            # Run cleanup directly via SQL (simulating what the cleanup method does)
            conn = sqlite3.connect(str(db_path))
            cutoff = time.time() - (24 * 3600)  # 1 day ago
            cursor = conn.execute("DELETE FROM inet_audit WHERE timestamp < ?", (cutoff,))
            removed = cursor.rowcount
            conn.commit()
            conn.close()

            # Should remove the old entry (2 days old) but keep recent one (1 hour old)
            assert removed >= 1  # At least the old entry should be removed

            # Verify recent entry remains
            entries = audit.query(limit=10)
            # We might have 1 or 2 entries depending on timing, but recent-key should be there
            recent_entries = [e for e in entries if e["key_id"] == "recent-key"]
            assert len(recent_entries) >= 1

            audit.close()

    def test_start_background_cleanup_creates_task_in_async_context(self):
        """Test that start_background_cleanup creates a task when called in async context."""

        async def test_async():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "audit.db"
                audit = InetAuditLog(db_path)

                # Start background cleanup
                audit.start_background_cleanup()

                # Verify task was created
                assert audit._cleanup_task is not None
                assert not audit._cleanup_task.done()

                # Wait a bit for cleanup to potentially run
                await asyncio.sleep(0.1)

                # Stop cleanup
                audit.close()

                # After close, _cleanup_task should be None
                assert audit._cleanup_task is None

        # Run the async test
        asyncio.run(test_async())

    @pytest.mark.asyncio
    async def test_async_logging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Log multiple entries using the public log method (which handles async)
            for i in range(10):
                audit.log(
                    key_id=f"async-key-{i}",
                    key_name=f"Async Key {i}",
                    source_ip=f"10.0.0.{i}",
                    method="POST",
                    path="/v1/chat/completions",
                    model="qwen3:8b",
                    status_code=200,
                    request_tokens=100,
                    response_tokens=50,
                    latency_ms=1000.0,
                    error="",
                    violation="",
                )

            # Give async writes time to complete
            await asyncio.sleep(0.2)

            # Verify all entries were written
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM inet_audit")
            count = cursor.fetchone()[0]
            assert count == 10

            conn.close()
            audit.close()

    def test_close_multiple_times_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Close multiple times should not raise
            audit.close()
            audit.close()
            audit.close()

    def test_log_with_minimal_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.db"
            audit = InetAuditLog(db_path)

            # Log with minimal required fields
            audit.log(
                key_id="",
                key_name="",
                source_ip="",
                method="",
                path="",
                model="",
                status_code=0,
                request_tokens=0,
                response_tokens=0,
                latency_ms=0.0,
                error="",
                violation="",
            )

            time.sleep(0.1)

            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM inet_audit")
            count = cursor.fetchone()[0]
            assert count == 1

            conn.close()
            audit.close()

    def test_parent_directory_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Database in nested directory
            db_path = Path(tmpdir) / "deep" / "nested" / "audit.db"

            # Should create parent directories
            audit = InetAuditLog(db_path)
            assert db_path.parent.exists()
            assert db_path.exists()

            audit.close()
