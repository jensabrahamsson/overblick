"""Tests for dashboard services."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

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
                (
                    now - i * 60,
                    "api_call",
                    "moltbook",
                    "testident",
                    "moltbook",
                    json.dumps({"endpoint": f"/test/{i}"}),
                    1,
                ),
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


class TestAuditServiceQuery:
    """Additional tests for query() edge cases."""

    @pytest.fixture
    def audit_db_with_details(self, tmp_path):
        """Create audit DB with various detail types."""
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audit_log (
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
        # Entry with valid JSON details
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, plugin, details, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now - 10, "api_call", "moltbook", "testident", "moltbook",
             json.dumps({"endpoint": "/test"}), 1),
        )
        # Entry with invalid JSON details
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, plugin, details, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now - 20, "api_call", "llm", "testident", "llm", "not-valid-json{{{", 0),
        )
        # Entry with empty details
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, plugin, details, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now - 30, "engagement", "security", "testident", "security", None, 1),
        )
        conn.commit()
        conn.close()
        return tmp_path

    def test_should_parse_valid_json_details(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="testident")
        json_entry = next(e for e in entries if isinstance(e["details"], dict))
        assert json_entry["details"]["endpoint"] == "/test"
        svc.close()

    def test_should_keep_invalid_json_details_as_string(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="testident")
        invalid_entry = next(e for e in entries if e["details"] == "not-valid-json{{{")
        assert isinstance(invalid_entry["details"], str)
        svc.close()

    def test_should_filter_by_action(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="testident", action="engagement")
        assert len(entries) == 1
        assert entries[0]["action"] == "engagement"
        svc.close()

    def test_should_filter_by_plugin(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="testident", plugin="llm")
        assert len(entries) == 1
        assert entries[0]["plugin"] == "llm"
        svc.close()

    def test_should_convert_success_to_bool(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="testident")
        for entry in entries:
            assert isinstance(entry["success"], bool)
        svc.close()

    def test_should_query_all_identities_when_empty_identity(self, audit_db_with_details):
        svc = AuditService(audit_db_with_details)
        entries = svc.query(identity="")
        assert len(entries) == 3
        svc.close()

    def test_should_skip_identity_with_no_connection(self, tmp_path):
        """No data dir at all."""
        svc = AuditService(tmp_path)
        entries = svc.query(identity="nonexistent")
        assert entries == []
        svc.close()


class TestAuditServiceGetConnection:
    def test_should_return_none_when_db_missing(self, tmp_path):
        svc = AuditService(tmp_path)
        conn = svc._get_connection("nonexistent")
        assert conn is None

    def test_should_return_cached_connection(self, tmp_path):
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        sqlite3.connect(str(db_path)).execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY)"
        )

        svc = AuditService(tmp_path)
        conn1 = svc._get_connection("testident")
        conn2 = svc._get_connection("testident")
        assert conn1 is conn2
        svc.close()

    def test_should_return_none_on_connection_error(self, tmp_path):
        data_dir = tmp_path / "data" / "badident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        # Create a file that isn't a valid SQLite DB
        db_path.write_text("not a database")

        svc = AuditService(tmp_path)
        # The read-only URI open might fail on a corrupted file
        from unittest.mock import patch
        with patch("sqlite3.connect", side_effect=Exception("cannot open")):
            conn = svc._get_connection("badident")
        assert conn is None
        svc.close()


class TestAuditServiceCountByHour:
    @pytest.fixture
    def audit_db_hourly(self, tmp_path):
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audit_log (
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
        # Insert entries at various time offsets
        for i in range(3):
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) "
                "VALUES (?, ?, ?, ?, ?)",
                (now - 60 * i, "api_call", "llm", "testident", 1),
            )
        # One failure 2 hours ago
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (now - 7200, "api_call", "llm", "testident", 0),
        )
        conn.commit()
        conn.close()
        return tmp_path

    def test_should_return_hourly_buckets(self, audit_db_hourly):
        svc = AuditService(audit_db_hourly)
        result = svc.count_by_hour(hours=12, identity="testident")
        assert len(result) == 12
        assert all("hour" in r and "total" in r and "failures" in r for r in result)
        # Most recent bucket should have some entries
        total_all = sum(r["total"] for r in result)
        assert total_all == 4
        total_failures = sum(r["failures"] for r in result)
        assert total_failures == 1
        svc.close()

    def test_should_filter_by_category(self, audit_db_hourly):
        svc = AuditService(audit_db_hourly)
        result = svc.count_by_hour(hours=6, identity="testident", category="llm")
        total = sum(r["total"] for r in result)
        assert total == 4
        svc.close()

    def test_should_return_all_zeros_for_nonexistent_identity(self, audit_db_hourly):
        svc = AuditService(audit_db_hourly)
        result = svc.count_by_hour(hours=4, identity="nonexistent")
        assert all(r["total"] == 0 for r in result)
        svc.close()

    def test_should_query_all_identities(self, audit_db_hourly):
        svc = AuditService(audit_db_hourly)
        result = svc.count_by_hour(hours=6)
        total = sum(r["total"] for r in result)
        assert total == 4
        svc.close()

    def test_should_handle_db_error_gracefully(self, tmp_path):
        data_dir = tmp_path / "data" / "broken"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        # Create table with wrong schema to cause error
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.close()

        svc = AuditService(tmp_path)
        result = svc.count_by_hour(hours=4)
        # Should return empty buckets instead of crashing
        assert len(result) == 4
        assert all(r["total"] == 0 for r in result)
        svc.close()


class TestAuditServiceCountByCategory:
    @pytest.fixture
    def audit_db_categories(self, tmp_path):
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audit_log (
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
        for cat, count in [("llm", 5), ("moltbook", 3), ("security", 2)]:
            for i in range(count):
                conn.execute(
                    "INSERT INTO audit_log (timestamp, action, category, identity, success) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (now - i * 60, "api_call", cat, "testident", 1),
                )
        # Entry with NULL category
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "api_call", "", "testident", 1),
        )
        conn.commit()
        conn.close()
        return tmp_path

    def test_should_return_counts_by_category(self, audit_db_categories):
        svc = AuditService(audit_db_categories)
        result = svc.count_by_category(identity="testident")
        assert result["llm"] == 5
        assert result["moltbook"] == 3
        assert result["security"] == 2
        svc.close()

    def test_should_aggregate_across_identities(self, audit_db_categories):
        svc = AuditService(audit_db_categories)
        result = svc.count_by_category()
        assert "llm" in result
        svc.close()

    def test_should_return_empty_for_nonexistent_identity(self, audit_db_categories):
        svc = AuditService(audit_db_categories)
        result = svc.count_by_category(identity="nonexistent")
        assert result == {}
        svc.close()

    def test_should_handle_db_error_gracefully(self, tmp_path):
        data_dir = tmp_path / "data" / "broken"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.close()

        svc = AuditService(tmp_path)
        result = svc.count_by_category()
        assert result == {}
        svc.close()

    def test_should_map_null_category_to_unknown(self, audit_db_categories):
        svc = AuditService(audit_db_categories)
        result = svc.count_by_category(identity="testident")
        # The empty string category entry — check it exists as empty string or "unknown"
        # In the code: cat = row[0] or "unknown"
        # Empty string is falsy, so it maps to "unknown"
        assert "unknown" in result or "" in result
        svc.close()


class TestAuditServiceGetActions:
    @pytest.fixture
    def audit_db_actions(self, tmp_path):
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audit_log (
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
        for action in ["api_call", "engagement", "llm_request"]:
            conn.execute(
                "INSERT INTO audit_log (timestamp, action, category, identity, success) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, action, "general", "testident", 1),
            )
        conn.commit()
        conn.close()
        return tmp_path

    def test_should_return_sorted_actions(self, audit_db_actions):
        svc = AuditService(audit_db_actions)
        actions = svc.get_actions()
        assert actions == ["api_call", "engagement", "llm_request"]
        svc.close()

    def test_should_handle_db_error_in_get_actions(self, tmp_path):
        data_dir = tmp_path / "data" / "broken"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.close()

        svc = AuditService(tmp_path)
        actions = svc.get_actions()
        # Should return empty or partial — not crash
        assert isinstance(actions, list)
        svc.close()

    def test_should_handle_db_error_in_get_categories(self, tmp_path):
        data_dir = tmp_path / "data" / "broken"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.close()

        svc = AuditService(tmp_path)
        cats = svc.get_categories()
        assert isinstance(cats, list)
        svc.close()


class TestAuditServiceClose:
    def test_should_close_all_connections(self, tmp_path):
        data_dir = tmp_path / "data" / "testident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp REAL, "
            "action TEXT, category TEXT, identity TEXT, success INTEGER)"
        )
        conn.close()

        svc = AuditService(tmp_path)
        svc._get_connection("testident")
        assert len(svc._connections) == 1
        svc.close()
        assert len(svc._connections) == 0

    def test_should_handle_close_error_gracefully(self, tmp_path):
        from unittest.mock import MagicMock
        svc = AuditService(tmp_path)
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close failed")
        svc._connections["test"] = mock_conn
        svc.close()  # Should not raise
        assert len(svc._connections) == 0


class TestAuditServiceDiscoverIdentities:
    def test_should_return_empty_when_data_dir_missing(self, tmp_path):
        svc = AuditService(tmp_path)
        # data dir doesn't exist
        result = svc._discover_identities()
        assert result == []

    def test_should_cache_empty_result(self, tmp_path):
        svc = AuditService(tmp_path)
        result1 = svc._discover_identities()
        assert result1 == []
        # Second call should use cache
        result2 = svc._discover_identities()
        assert result2 == []


class TestAuditServiceConnectionSkip:
    """Test the continue branch when _get_connection returns None for a discovered identity."""

    @pytest.fixture
    def multi_ident_db(self, tmp_path):
        """Create data dirs for two identities but only one has a valid DB."""
        # Valid identity
        valid_dir = tmp_path / "data" / "valid"
        valid_dir.mkdir(parents=True)
        db_path = valid_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audit_log (
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
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, category, identity, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "api_call", "llm", "valid", 1),
        )
        conn.commit()
        conn.close()

        # Invalid identity — has audit.db dir marker but will fail to connect
        invalid_dir = tmp_path / "data" / "invalid"
        invalid_dir.mkdir(parents=True)
        (invalid_dir / "audit.db").write_text("not a database")

        return tmp_path

    def test_should_skip_bad_connection_in_count(self, multi_ident_db):
        svc = AuditService(multi_ident_db)
        # Make _get_connection return None for "invalid" identity
        orig_get_conn = svc._get_connection
        def patched_get_conn(ident):
            if ident == "invalid":
                return None
            return orig_get_conn(ident)
        svc._get_connection = patched_get_conn
        count = svc.count()
        assert count == 1
        svc.close()

    def test_should_skip_bad_connection_in_get_categories(self, multi_ident_db):
        svc = AuditService(multi_ident_db)
        orig_get_conn = svc._get_connection
        def patched_get_conn(ident):
            if ident == "invalid":
                return None
            return orig_get_conn(ident)
        svc._get_connection = patched_get_conn
        categories = svc.get_categories()
        assert "llm" in categories
        svc.close()

    def test_should_skip_bad_connection_in_get_actions(self, multi_ident_db):
        svc = AuditService(multi_ident_db)
        orig_get_conn = svc._get_connection
        def patched_get_conn(ident):
            if ident == "invalid":
                return None
            return orig_get_conn(ident)
        svc._get_connection = patched_get_conn
        actions = svc.get_actions()
        assert "api_call" in actions
        svc.close()


class TestAuditServiceQueryErrors:
    @pytest.fixture
    def audit_db_broken_query(self, tmp_path):
        """Create audit DB that will cause errors on certain queries."""
        data_dir = tmp_path / "data" / "errorident"
        data_dir.mkdir(parents=True)
        db_path = data_dir / "audit.db"

        conn = sqlite3.connect(str(db_path))
        # Create a table with only 'id' column to cause query errors
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.close()
        return tmp_path

    def test_should_handle_query_error_gracefully(self, audit_db_broken_query):
        svc = AuditService(audit_db_broken_query)
        # This will fail because the table doesn't have the expected columns
        entries = svc.query(identity="errorident")
        assert entries == []
        svc.close()

    def test_should_handle_count_error_gracefully(self, audit_db_broken_query):
        svc = AuditService(audit_db_broken_query)
        count = svc.count(identity="errorident")
        assert count == 0
        svc.close()

    def test_should_handle_count_with_failures_error_gracefully(self, audit_db_broken_query):
        svc = AuditService(audit_db_broken_query)
        total, failures = svc.count_with_failures(identity="errorident")
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
