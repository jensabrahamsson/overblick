"""
Abstract database backend interface.

All database operations go through this interface, allowing
seamless switching between SQLite and PostgreSQL.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Row type — dict-like access to column values
DatabaseRow = dict[str, Any]


@dataclass
class DatabaseConfig:
    """
    Database configuration.

    Supports both SQLite and PostgreSQL via a unified config structure.
    """
    backend: str = "sqlite"  # "sqlite" or "postgresql"

    # SQLite settings
    sqlite_path: str = "data/{identity}/blick.db"

    # PostgreSQL settings
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "blick"
    pg_user: str = "blick"
    pg_password: str = ""
    pg_schema: str = "public"

    # Connection pool settings (PostgreSQL)
    pool_min_size: int = 1
    pool_max_size: int = 10

    # Shared settings
    echo_sql: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatabaseConfig":
        """Create config from a dictionary (typically from YAML)."""
        config = cls()
        config.backend = data.get("backend", "sqlite")

        sqlite = data.get("sqlite", {})
        config.sqlite_path = sqlite.get("path", config.sqlite_path)

        pg = data.get("postgresql", {})
        config.pg_host = pg.get("host", config.pg_host)
        config.pg_port = pg.get("port", config.pg_port)
        config.pg_database = pg.get("database", config.pg_database)
        config.pg_user = pg.get("user", config.pg_user)
        config.pg_password = pg.get("password", config.pg_password)
        config.pg_schema = pg.get("schema", config.pg_schema)

        pool = data.get("pool", {})
        config.pool_min_size = pool.get("min_size", config.pool_min_size)
        config.pool_max_size = pool.get("max_size", config.pool_max_size)

        config.echo_sql = data.get("echo_sql", False)
        return config


class DatabaseBackend(ABC):
    """
    Abstract database backend.

    Provides a unified async interface for database operations.
    Both SQLite and PostgreSQL backends implement this interface.

    Placeholder syntax differs:
    - SQLite: ? (positional)
    - PostgreSQL: $1, $2, ... (numbered)

    Use `self.ph(n)` to get the correct placeholder for position n.
    """

    def __init__(self, config: DatabaseConfig, identity: str = ""):
        self._config = config
        self._identity = identity
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def backend_name(self) -> str:
        return self._config.backend

    @abstractmethod
    def ph(self, position: int) -> str:
        """
        Get placeholder for parameterized query at given position (1-based).

        SQLite returns '?', PostgreSQL returns '$1', '$2', etc.
        """

    @abstractmethod
    async def connect(self) -> None:
        """Establish database connection."""

    @abstractmethod
    async def close(self) -> None:
        """Close database connection and release resources."""

    @abstractmethod
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """
        Execute a SQL statement (INSERT, UPDATE, DELETE).

        Returns the number of affected rows.
        """

    @abstractmethod
    async def execute_returning_id(self, sql: str, params: Sequence[Any] = ()) -> Optional[int]:
        """
        Execute an INSERT and return the new row's ID.

        Returns the auto-generated ID, or None.
        """

    @abstractmethod
    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[DatabaseRow]:
        """Fetch a single row. Returns None if no results."""

    @abstractmethod
    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[DatabaseRow]:
        """Fetch all matching rows."""

    @abstractmethod
    async def fetch_scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Fetch a single scalar value. Returns None if no results."""

    @abstractmethod
    async def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script (for migrations)."""

    @abstractmethod
    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""


@dataclass
class Migration:
    """A single database migration."""
    version: int
    name: str
    up_sql: str
    down_sql: str = ""


class MigrationManager:
    """
    Simple migration manager for schema versioning.

    Tracks applied migrations in a _migrations table.
    """

    def __init__(self, db: DatabaseBackend):
        self._db = db

    async def setup(self) -> None:
        """Create the migrations tracking table if needed."""
        await self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS _migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

    async def current_version(self) -> int:
        """Get the current schema version."""
        if not await self._db.table_exists("_migrations"):
            return 0
        result = await self._db.fetch_scalar(
            "SELECT MAX(version) FROM _migrations"
        )
        return result or 0

    async def apply(self, migrations: list[Migration]) -> int:
        """
        Apply pending migrations in order.

        Returns the number of migrations applied.
        """
        await self.setup()
        current = await self.current_version()
        applied = 0

        for migration in sorted(migrations, key=lambda m: m.version):
            if migration.version <= current:
                continue

            logger.info(
                "Applying migration %d: %s", migration.version, migration.name
            )
            await self._db.execute_script(migration.up_sql)
            await self._db.execute(
                f"INSERT INTO _migrations (version, name) VALUES ({self._db.ph(1)}, {self._db.ph(2)})",
                (migration.version, migration.name),
            )
            applied += 1

        if applied:
            logger.info("Applied %d migration(s), now at version %d",
                        applied, await self.current_version())
        return applied
