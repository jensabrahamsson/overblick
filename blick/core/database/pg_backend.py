"""
PostgreSQL database backend.

Optional backend — requires asyncpg package:
    pip install blick[postgresql]

Uses async connection pooling for high-performance concurrent access.
"""

import logging
from typing import Any, Optional, Sequence

from blick.core.database.base import DatabaseBackend, DatabaseConfig, DatabaseRow

logger = logging.getLogger(__name__)

# asyncpg is optional — only required when PostgreSQL is configured
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    asyncpg = None
    HAS_ASYNCPG = False


class PostgreSQLBackend(DatabaseBackend):
    """
    PostgreSQL implementation of the DatabaseBackend.

    Uses asyncpg for fully async, high-performance database access.
    Connection pooling is built in.

    Requires: pip install asyncpg (or pip install blick[postgresql])
    """

    def __init__(self, config: DatabaseConfig, identity: str = ""):
        super().__init__(config, identity)
        self._pool = None

        if not HAS_ASYNCPG:
            raise ImportError(
                "PostgreSQL backend requires asyncpg. "
                "Install with: pip install blick[postgresql]"
            )

    def ph(self, position: int) -> str:
        """PostgreSQL uses $1, $2, ... placeholders (numbered)."""
        return f"${position}"

    async def connect(self) -> None:
        """Create connection pool."""
        dsn = (
            f"postgresql://{self._config.pg_user}:{self._config.pg_password}"
            f"@{self._config.pg_host}:{self._config.pg_port}"
            f"/{self._config.pg_database}"
        )

        self._pool = await asyncpg.create_pool(
            dsn,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
        )

        # Set schema if not public
        if self._config.pg_schema != "public":
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f"SET search_path TO {self._config.pg_schema}, public"
                )

        self._connected = True
        logger.info(
            "PostgreSQL connected: %s@%s:%d/%s",
            self._config.pg_user, self._config.pg_host,
            self._config.pg_port, self._config.pg_database,
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._connected = False

    def _check_connected(self) -> None:
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute SQL and return affected row count."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *params)
            # asyncpg returns "INSERT 0 1" style strings
            parts = result.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                return int(parts[-1])
            return 0

    async def execute_returning_id(self, sql: str, params: Sequence[Any] = ()) -> Optional[int]:
        """Execute INSERT with RETURNING and return the new row ID."""
        self._check_connected()
        # Ensure SQL has RETURNING clause for PostgreSQL
        if "RETURNING" not in sql.upper():
            sql = sql.rstrip(";") + " RETURNING id"

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return row[0] if row else None

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[DatabaseRow]:
        """Fetch a single row as a dict."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            if row is None:
                return None
            return dict(row)

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[DatabaseRow]:
        """Fetch all matching rows as dicts."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(row) for row in rows]

    async def fetch_scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Fetch a single scalar value."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *params)

    async def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in PostgreSQL."""
        self._check_connected()
        result = await self.fetch_scalar(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2",
            (self._config.pg_schema, table_name),
        )
        return result > 0
