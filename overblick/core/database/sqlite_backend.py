"""
SQLite database backend.

Default backend — no external dependencies required.
Uses synchronous sqlite3 offloaded to a single-thread executor
to avoid blocking the async event loop.
"""

import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional, Sequence

from overblick.core.database.base import DatabaseBackend, DatabaseConfig, DatabaseRow

logger = logging.getLogger(__name__)


class SQLiteBackend(DatabaseBackend):
    """
    SQLite implementation of the DatabaseBackend.

    Uses synchronous sqlite3 (Python stdlib) offloaded to executors
    to avoid blocking the async event loop.

    Thread safety model:
    - Writes go through a single-thread executor sharing self._conn
    - Reads open per-call connections (WAL mode supports concurrent readers)
    - This avoids sharing sqlite3.Connection across threads which is unsafe
    """

    def __init__(self, config: DatabaseConfig, identity: str = ""):
        super().__init__(config, identity)
        path_template = config.sqlite_path
        if identity:
            path_template = path_template.replace("{identity}", identity)
        self._db_path = Path(path_template)
        self._conn: Optional[sqlite3.Connection] = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sqlite-write",
        )
        # Read executor uses per-call connections (not shared _conn)
        # so concurrent reads are safe under WAL mode.
        self._read_executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="sqlite-read",
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def ph(self, position: int) -> str:
        """SQLite uses ? placeholders (positional)."""
        return "?"

    async def connect(self) -> None:
        """Open SQLite connection with WAL mode."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrency
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign keys
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._connected = True
        logger.info("SQLite connected: %s", self._db_path)

    async def close(self) -> None:
        """Close SQLite connection and executor."""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.warning("Error closing SQLite: %s", e)
            finally:
                self._conn = None
        self._executor.shutdown(wait=True)
        self._read_executor.shutdown(wait=True)
        self._connected = False

    def _check_connected(self) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected. Call connect() first.")

    async def _run_in_executor(self, fn, *args):
        """Run a write operation in the dedicated sqlite write thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    async def _run_in_read_executor(self, fn, *args):
        """Run a read operation in the read thread pool (up to 3 concurrent)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._read_executor, fn, *args)

    def _open_read_connection(self) -> sqlite3.Connection:
        """Open a read-only connection for thread-safe concurrent reads."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # --- Write operations (use shared _conn via single-thread executor) ---

    def _execute_sync(self, sql: str, params: Sequence[Any]) -> int:
        with self._conn:
            cursor = self._conn.execute(sql, params)
            return cursor.rowcount

    def _execute_returning_id_sync(self, sql: str, params: Sequence[Any]) -> Optional[int]:
        with self._conn:
            cursor = self._conn.execute(sql, params)
            return cursor.lastrowid

    # --- Read operations (per-call connections for thread safety) ---

    def _fetch_one_sync(self, sql: str, params: Sequence[Any]) -> Optional[DatabaseRow]:
        conn = self._open_read_connection()
        try:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def _fetch_all_sync(self, sql: str, params: Sequence[Any]) -> list[DatabaseRow]:
        conn = self._open_read_connection()
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _fetch_scalar_sync(self, sql: str, params: Sequence[Any]) -> Any:
        conn = self._open_read_connection()
        try:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            return row[0]
        finally:
            conn.close()

    # --- Async interface ---

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute SQL and return affected row count."""
        self._check_connected()
        return await self._run_in_executor(self._execute_sync, sql, params)

    async def execute_returning_id(self, sql: str, params: Sequence[Any] = ()) -> Optional[int]:
        """Execute INSERT and return the new row ID."""
        self._check_connected()
        return await self._run_in_executor(self._execute_returning_id_sync, sql, params)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[DatabaseRow]:
        """Fetch a single row as a dict (uses per-call read connection)."""
        self._check_connected()
        return await self._run_in_read_executor(self._fetch_one_sync, sql, params)

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[DatabaseRow]:
        """Fetch all matching rows as dicts (uses per-call read connection)."""
        self._check_connected()
        return await self._run_in_read_executor(self._fetch_all_sync, sql, params)

    async def fetch_scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Fetch a single scalar value (uses per-call read connection)."""
        self._check_connected()
        return await self._run_in_read_executor(self._fetch_scalar_sync, sql, params)

    def _execute_many_sync(self, sql: str, params_list: list[Sequence[Any]]) -> int:
        with self._conn:
            self._conn.executemany(sql, params_list)
            return len(params_list)

    async def execute_many(self, sql: str, params_list: list[Sequence[Any]]) -> int:
        """Batch execute using SQLite's native executemany (single transaction)."""
        self._check_connected()
        if not params_list:
            return 0
        return await self._run_in_executor(self._execute_many_sync, sql, params_list)

    def _execute_script_sync(self, sql: str) -> None:
        """Execute a multi-statement SQL script (sync helper)."""
        self._conn.executescript(sql)

    async def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script."""
        self._check_connected()
        await self._run_in_executor(self._execute_script_sync, sql)

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the SQLite database."""
        self._check_connected()
        result = await self.fetch_scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return result > 0
