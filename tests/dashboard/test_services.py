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
