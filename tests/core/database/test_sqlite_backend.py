"""Tests for sqlite_backend.py — cover close() error path and fetch_scalar None."""

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend


class TestSQLiteBackendCloseError:
    """Cover lines 82-83: exception during close logs warning."""

    @pytest.mark.asyncio
    async def test_should_log_warning_when_close_raises(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "err.db"))
        backend = SQLiteBackend(config)
        await backend.connect()

        # Replace _conn with a mock that raises on close()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = sqlite3.OperationalError("disk I/O error")
        # Truthy so the if-branch is entered
        mock_conn.__bool__ = lambda self: True
        backend._conn = mock_conn

        with patch("overblick.core.database.sqlite_backend.logger") as mock_logger:
            await backend.close()
            mock_logger.warning.assert_called_once()
            assert "disk I/O error" in str(mock_logger.warning.call_args)

        # Connection should be None after close even on error
        assert backend._conn is None
        assert not backend.connected


class TestSQLiteBackendFetchScalarNone:
    """Cover line 152: fetch_scalar returns None when no row found."""

    @pytest.mark.asyncio
    async def test_should_return_none_when_fetch_scalar_finds_no_rows(self, tmp_path):
        config = DatabaseConfig(sqlite_path=str(tmp_path / "scalar.db"))
        backend = SQLiteBackend(config)
        await backend.connect()
        await backend.execute_script("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT);")

        result = await backend.fetch_scalar("SELECT val FROM t WHERE id = ?", (999,))
        assert result is None

        await backend.close()
