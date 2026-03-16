"""Tests for SQLiteBanStore persistent ban storage."""

import time
from collections.abc import Generator
from pathlib import Path

import pytest

from overblick.gateway.inet_violation_db import SQLiteBanStore


@pytest.fixture
def ban_store(tmp_path: Path) -> Generator[SQLiteBanStore]:
    """Create a ban store with temp database."""
    store = SQLiteBanStore(tmp_path / "bans.db")
    yield store
    store.close()


class TestSQLiteBanStore:
    """Tests for SQLiteBanStore CRUD operations."""

    def test_should_add_and_load_ban(self, ban_store: SQLiteBanStore):
        future = time.time() + 3600
        ban_store.add_ban("1.2.3.4", future)

        bans = ban_store.load_bans()
        assert "1.2.3.4" in bans
        assert bans["1.2.3.4"] == pytest.approx(future, abs=1)

    def test_should_remove_ban(self, ban_store: SQLiteBanStore):
        future = time.time() + 3600
        ban_store.add_ban("1.2.3.4", future)
        ban_store.remove_ban("1.2.3.4")

        bans = ban_store.load_bans()
        assert "1.2.3.4" not in bans

    def test_should_load_only_active_bans(self, ban_store: SQLiteBanStore):
        future = time.time() + 3600
        past = time.time() - 100

        ban_store.add_ban("1.1.1.1", future)  # active
        ban_store.add_ban("2.2.2.2", past)  # expired

        bans = ban_store.load_bans()
        assert "1.1.1.1" in bans
        assert "2.2.2.2" not in bans

    def test_should_cleanup_expired_bans(self, ban_store: SQLiteBanStore):
        past = time.time() - 100
        future = time.time() + 3600

        ban_store.add_ban("1.1.1.1", past)  # expired
        ban_store.add_ban("2.2.2.2", past)  # expired
        ban_store.add_ban("3.3.3.3", future)  # active

        deleted = ban_store.cleanup_expired()
        assert deleted == 2

        bans = ban_store.load_bans()
        assert "3.3.3.3" in bans
        assert len(bans) == 1

    def test_should_cleanup_return_zero_when_nothing_expired(self, ban_store: SQLiteBanStore):
        future = time.time() + 3600
        ban_store.add_ban("1.1.1.1", future)

        deleted = ban_store.cleanup_expired()
        assert deleted == 0

    def test_should_replace_existing_ban(self, ban_store: SQLiteBanStore):
        future1 = time.time() + 1000
        future2 = time.time() + 5000

        ban_store.add_ban("1.2.3.4", future1)
        ban_store.add_ban("1.2.3.4", future2)

        bans = ban_store.load_bans()
        assert bans["1.2.3.4"] == pytest.approx(future2, abs=1)

    def test_should_remove_nonexistent_ban_without_error(self, ban_store: SQLiteBanStore):
        ban_store.remove_ban("nonexistent")  # should not raise

    def test_should_load_empty_when_no_bans(self, ban_store: SQLiteBanStore):
        bans = ban_store.load_bans()
        assert bans == {}

    def test_should_close_all_connections(self, tmp_path: Path):
        store = SQLiteBanStore(tmp_path / "close_test.db")
        store.add_ban("1.1.1.1", time.time() + 3600)
        store.close()
        # After close, connections list should be empty
        assert store._connections == []

    def test_should_close_with_thread_local_conn(self, tmp_path: Path):
        store = SQLiteBanStore(tmp_path / "thread_test.db")
        # Access through _get_conn to create a thread-local connection
        store._get_conn()
        assert hasattr(store._local, "conn")
        store.close()
        assert not hasattr(store._local, "conn")

    def test_should_create_parent_directories(self, tmp_path: Path):
        nested_path = tmp_path / "deep" / "nested" / "bans.db"
        store = SQLiteBanStore(nested_path)
        store.add_ban("1.1.1.1", time.time() + 3600)
        store.close()
        assert nested_path.exists()

    def test_should_handle_close_with_broken_connection(self, tmp_path: Path):
        from unittest.mock import MagicMock

        store = SQLiteBanStore(tmp_path / "broken.db")
        store._get_conn()

        # Replace the real connection with one that raises on close
        broken_conn = MagicMock()
        broken_conn.close.side_effect = Exception("connection broken")
        store._connections = [broken_conn]

        # close() should handle the error gracefully (not raise)
        store.close()
        assert store._connections == []
