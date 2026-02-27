"""Tests for dashboard services."""

import sqlite3
import json
import time
import pytest
from pathlib import Path

from overblick.dashboard.services.audit import AuditService


class TestAuditService:
    @pytest.fixture
    def audit_db(self, tmp_path):
        """Create a test audit database."""
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

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
                error TEXT
            );
        """)

        # Insert test data
        now = time.time()
        for i in range(5):
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, action, category, identity, plugin, details, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now - i * 60, "api_call", "moltbook", "testident", "moltbook",
                 json.dumps({"endpoint": f"/test/{i}"}), 1),
            )
        conn.commit()
        conn.close()
        return tmp_path

    def test_query_returns_entries(self, audit_db):
        svc = AuditService(audit_db)
        entries = svc.query(identity="testident")
        assert len(entries) == 5
        svc.close()

    def test_query_with_limit(self, audit_db):
        svc = AuditService(audit_db)
        entries = svc.query(identity="testident", limit=3)
        assert len(entries) == 3
        svc.close()

    def test_query_with_category_filter(self, audit_db):
        svc = AuditService(audit_db)
        entries = svc.query(identity="testident", category="moltbook")
        assert len(entries) == 5
        entries = svc.query(identity="testident", category="nonexistent")
        assert len(entries) == 0
        svc.close()

    def test_count(self, audit_db):
        svc = AuditService(audit_db)
        count = svc.count(identity="testident")
        assert count == 5
        svc.close()

    def test_count_with_category(self, audit_db):
        svc = AuditService(audit_db)
        count = svc.count(identity="testident", category="moltbook")
        assert count == 5
        count = svc.count(identity="testident", category="nonexistent")
        assert count == 0
        svc.close()

    def test_count_with_success_filter(self, audit_db):
        svc = AuditService(audit_db)
        count = svc.count(identity="testident", success=True)
        assert count == 5
        count = svc.count(identity="testident", success=False)
        assert count == 0
        svc.close()

    def test_discover_identities(self, audit_db):
        svc = AuditService(audit_db)
        identities = svc._discover_identities()
        assert "testident" in identities
        svc.close()

    def test_get_categories(self, audit_db):
        svc = AuditService(audit_db)
        categories = svc.get_categories()
        assert "moltbook" in categories
        svc.close()

    def test_read_only_mode(self, audit_db):
        """Verify that the read-only connection prevents writes."""
        svc = AuditService(audit_db)
        conn = svc._get_connection("testident")
        assert conn is not None

        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), "hack", "evil", "testident", 1),
            )
        svc.close()


class TestAuditServiceCountWithFailures:
    """Tests for count_with_failures() batch query."""

    @pytest.fixture
    def audit_mixed(self, tmp_path):
        """Create audit DB with mix of successes and failures."""
        data_dir = tmp_path / "data" / "mixed"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

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
                error TEXT
            );
        """)

        now = time.time()
        # 7 successes
        for i in range(7):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) "
                "VALUES (?, ?, ?, ?, ?)",
                (now - i * 60, "api_call", "llm", "mixed", 1),
            )
        # 3 failures
        for i in range(3):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now - (10 + i) * 60, "api_call", "llm", "mixed", 0, "timeout"),
            )
        conn.commit()
        conn.close()
        return tmp_path

    def test_returns_total_and_failures(self, audit_mixed):
        svc = AuditService(audit_mixed)
        total, failures = svc.count_with_failures(identity="mixed")
        assert total == 10
        assert failures == 3
        svc.close()

    def test_with_category_filter(self, audit_mixed):
        svc = AuditService(audit_mixed)
        total, failures = svc.count_with_failures(identity="mixed", category="llm")
        assert total == 10
        assert failures == 3
        total, failures = svc.count_with_failures(identity="mixed", category="nonexistent")
        assert total == 0
        assert failures == 0
        svc.close()

    def test_nonexistent_identity(self, audit_mixed):
        svc = AuditService(audit_mixed)
        total, failures = svc.count_with_failures(identity="nonexistent")
        assert total == 0
        assert failures == 0
        svc.close()


class TestAuditServiceIdentityCache:
    """Tests for _discover_identities() TTL caching."""

    def test_cache_returns_same_result(self, tmp_path):
        """Cached result should be returned within TTL."""
        data_dir = tmp_path / "data" / "cached"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp REAL, "
            "action TEXT, category TEXT, identity TEXT, success INTEGER)"
        )
        conn.close()

        svc = AuditService(tmp_path)
        result1 = svc._discover_identities()
        assert "cached" in result1

        # Add another identity dir
        new_dir = tmp_path / "data" / "newident"
        new_dir.mkdir(parents=True)
        new_db = new_dir / "audit.db"
        conn2 = sqlite3.connect(str(new_db))
        conn2.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp REAL, "
            "action TEXT, category TEXT, identity TEXT, success INTEGER)"
        )
        conn2.close()

        # Should still return cached result (within TTL)
        result2 = svc._discover_identities()
        assert "newident" not in result2  # Cached — doesn't see new dir

        # Force cache expiration
        svc._identity_cache_ts = 0.0
        result3 = svc._discover_identities()
        assert "newident" in result3  # Now sees new dir
        svc.close()
