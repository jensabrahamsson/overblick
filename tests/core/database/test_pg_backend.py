"""Tests for pg_backend.py — fully mocked asyncpg, no real PostgreSQL needed."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.pg_backend import PostgreSQLBackend, _SAFE_IDENTIFIER_RE


def _make_config(**overrides: Any) -> DatabaseConfig:
    defaults = {
        "backend": "postgresql",
        "pg_host": "localhost",
        "pg_port": 5432,
        "pg_database": "testdb",
        "pg_user": "testuser",
        "pg_password": "secret",
        "pg_schema": "public",
    }
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


@pytest.fixture(autouse=True)
def _mock_has_asyncpg():
    """Patch HAS_ASYNCPG to True and asyncpg to a mock so the module works without it."""
    mock_asyncpg = MagicMock()
    with patch("overblick.core.database.pg_backend.HAS_ASYNCPG", True), \
         patch("overblick.core.database.pg_backend.asyncpg", mock_asyncpg):
        yield mock_asyncpg


def _make_pool_with_conn(mock_conn: AsyncMock) -> MagicMock:
    """Build a mock pool whose acquire() context manager yields mock_conn."""
    mock_pool = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = ctx
    mock_pool.close = AsyncMock()
    return mock_pool


class TestPostgreSQLBackendInit:

    def test_should_use_dollar_placeholders(self):
        backend = PostgreSQLBackend(_make_config())
        assert backend.ph(1) == "$1"
        assert backend.ph(5) == "$5"

    def test_should_report_postgresql_backend_name(self):
        backend = PostgreSQLBackend(_make_config())
        assert backend.backend_name == "postgresql"

    def test_should_raise_when_asyncpg_not_installed(self):
        with patch("overblick.core.database.pg_backend.HAS_ASYNCPG", False):
            with pytest.raises(ImportError, match="asyncpg"):
                PostgreSQLBackend(_make_config())


class TestPostgreSQLBackendConnect:

    @pytest.mark.asyncio
    async def test_should_connect_with_public_schema(self, _mock_has_asyncpg):
        backend = PostgreSQLBackend(_make_config())
        mock_pool = AsyncMock()
        _mock_has_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
        await backend.connect()
        assert backend.connected
        assert backend._pool is mock_pool

    @pytest.mark.asyncio
    async def test_should_set_search_path_when_non_public_schema(self, _mock_has_asyncpg):
        backend = PostgreSQLBackend(_make_config(pg_schema="myapp"))
        captured_init = None

        async def capture_create_pool(**kwargs: Any) -> AsyncMock:
            nonlocal captured_init
            captured_init = kwargs.get("init")
            return AsyncMock()

        _mock_has_asyncpg.create_pool = capture_create_pool
        await backend.connect()

        assert captured_init is not None
        mock_conn = AsyncMock()
        await captured_init(mock_conn)
        mock_conn.execute.assert_called_once_with("SET search_path TO myapp, public")

    @pytest.mark.asyncio
    async def test_should_not_set_search_path_when_public_schema(self, _mock_has_asyncpg):
        backend = PostgreSQLBackend(_make_config(pg_schema="public"))
        captured_init = None

        async def capture_create_pool(**kwargs: Any) -> AsyncMock:
            nonlocal captured_init
            captured_init = kwargs.get("init")
            return AsyncMock()

        _mock_has_asyncpg.create_pool = capture_create_pool
        await backend.connect()

        mock_conn = AsyncMock()
        await captured_init(mock_conn)
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_reject_unsafe_schema_name(self):
        backend = PostgreSQLBackend(_make_config(pg_schema="DROP TABLE; --"))
        with pytest.raises(ValueError, match="Unsafe PostgreSQL schema name"):
            await backend.connect()


class TestPostgreSQLBackendClose:

    @pytest.mark.asyncio
    async def test_should_close_pool(self):
        backend = PostgreSQLBackend(_make_config())
        mock_pool = AsyncMock()
        backend._pool = mock_pool
        backend._connected = True

        await backend.close()
        mock_pool.close.assert_awaited_once()
        assert backend._pool is None
        assert not backend.connected

    @pytest.mark.asyncio
    async def test_should_handle_close_when_no_pool(self):
        backend = PostgreSQLBackend(_make_config())
        await backend.close()
        assert backend._pool is None
        assert not backend.connected


class TestPostgreSQLBackendCheckConnected:

    def test_should_raise_when_not_connected(self):
        backend = PostgreSQLBackend(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            backend._check_connected()


class TestPostgreSQLBackendExecute:

    @pytest.mark.asyncio
    async def test_should_return_affected_rows_from_insert(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="INSERT 0 3")
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.execute("INSERT INTO t VALUES ($1)", ("a",))
        assert result == 3

    @pytest.mark.asyncio
    async def test_should_return_zero_when_result_has_no_digit(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="SET")
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.execute("SET search_path TO public")
        assert result == 0

    @pytest.mark.asyncio
    async def test_should_return_rows_from_delete(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 5")
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.execute("DELETE FROM t WHERE id > $1", (0,))
        assert result == 5


class TestPostgreSQLBackendExecuteReturningId:

    @pytest.mark.asyncio
    async def test_should_return_id_from_returning_clause(self):
        backend = PostgreSQLBackend(_make_config())
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(return_value=42)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.execute_returning_id(
            "INSERT INTO t (name) VALUES ($1) RETURNING id", ("test",)
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_should_append_returning_clause_when_missing(self):
        backend = PostgreSQLBackend(_make_config())
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(return_value=7)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        backend._pool = _make_pool_with_conn(mock_conn)

        await backend.execute_returning_id("INSERT INTO t (name) VALUES ($1)", ("test",))
        called_sql = mock_conn.fetchrow.call_args[0][0]
        assert "RETURNING id" in called_sql

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_row_returned(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.execute_returning_id(
            "INSERT INTO t (name) VALUES ($1) RETURNING id", ("test",)
        )
        assert result is None


class TestPostgreSQLBackendFetchOne:

    @pytest.mark.asyncio
    async def test_should_return_dict_when_row_found(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        # asyncpg Record supports dict() — use a real dict as return value
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1, "name": "test"})
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.fetch_one("SELECT * FROM t WHERE id = $1", (1,))
        assert result == {"id": 1, "name": "test"}

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_row(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.fetch_one("SELECT * FROM t WHERE id = $1", (999,))
        assert result is None


class TestPostgreSQLBackendFetchAll:

    @pytest.mark.asyncio
    async def test_should_return_list_of_dicts(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.fetch_all("SELECT * FROM t")
        assert result == [{"id": 1}, {"id": 2}]


class TestPostgreSQLBackendFetchScalar:

    @pytest.mark.asyncio
    async def test_should_return_scalar_value(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=42)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.fetch_scalar("SELECT COUNT(*) FROM t")
        assert result == 42


class TestPostgreSQLBackendExecuteScript:

    @pytest.mark.asyncio
    async def test_should_execute_script(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        backend._pool = _make_pool_with_conn(mock_conn)

        await backend.execute_script("CREATE TABLE t (id INT);")
        mock_conn.execute.assert_awaited_once_with("CREATE TABLE t (id INT);")


class TestPostgreSQLBackendTableExists:

    @pytest.mark.asyncio
    async def test_should_return_true_when_table_exists(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.table_exists("my_table")
        assert result is True

    @pytest.mark.asyncio
    async def test_should_return_false_when_table_missing(self):
        backend = PostgreSQLBackend(_make_config())
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=0)
        backend._pool = _make_pool_with_conn(mock_conn)

        result = await backend.table_exists("nope")
        assert result is False


class TestAsyncpgImportSuccess:

    def test_should_set_has_asyncpg_true_when_asyncpg_available(self):
        """Cover line 28: HAS_ASYNCPG = True when asyncpg imports successfully."""
        import importlib
        import sys

        mock_asyncpg = MagicMock()
        mock_asyncpg.__name__ = "asyncpg"
        # Temporarily inject mock asyncpg into sys.modules
        original = sys.modules.get("asyncpg")
        sys.modules["asyncpg"] = mock_asyncpg
        try:
            import overblick.core.database.pg_backend as mod
            importlib.reload(mod)
            assert mod.HAS_ASYNCPG is True
        finally:
            # Restore
            if original is None:
                sys.modules.pop("asyncpg", None)
            else:
                sys.modules["asyncpg"] = original
            import overblick.core.database.pg_backend as mod2
            importlib.reload(mod2)


class TestSafeIdentifierRegex:

    def test_should_match_valid_identifiers(self):
        assert _SAFE_IDENTIFIER_RE.match("public")
        assert _SAFE_IDENTIFIER_RE.match("my_schema")
        assert _SAFE_IDENTIFIER_RE.match("_private")
        assert _SAFE_IDENTIFIER_RE.match("Schema123")

    def test_should_reject_invalid_identifiers(self):
        assert not _SAFE_IDENTIFIER_RE.match("123start")
        assert not _SAFE_IDENTIFIER_RE.match("drop; --")
        assert not _SAFE_IDENTIFIER_RE.match("")
        assert not _SAFE_IDENTIFIER_RE.match("a" * 64)
