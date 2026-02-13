"""
Database abstraction layer — supports SQLite and PostgreSQL.

Configurable at instance setup:
    database:
      backend: sqlite        # or "postgresql"
      sqlite:
        path: "data/{identity}/overblick.db"
      postgresql:
        host: localhost
        port: 5432
        database: overblick
        user: overblick
        password: secret

Usage:
    from overblick.core.database import create_backend, DatabaseBackend
    db = create_backend(config)
    await db.connect()
    await db.execute("INSERT INTO ...", params)
    rows = await db.fetch_all("SELECT * FROM ...", params)
    await db.close()
"""

from overblick.core.database.base import (
    DatabaseBackend,
    DatabaseConfig,
    DatabaseRow,
    MigrationManager,
)
from overblick.core.database.factory import create_backend

__all__ = [
    "DatabaseBackend",
    "DatabaseConfig",
    "DatabaseRow",
    "MigrationManager",
    "create_backend",
]
