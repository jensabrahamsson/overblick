"""
Database backend factory — create the right backend from config.
"""

import logging
from typing import Any

from overblick.core.database.base import DatabaseBackend, DatabaseConfig

logger = logging.getLogger(__name__)


def create_backend(
    config: DatabaseConfig | dict[str, Any] | None = None,
    identity: str = "",
) -> DatabaseBackend:
    """
    Create a database backend based on configuration.

    Args:
        config: DatabaseConfig, raw dict, or None for defaults
        identity: Identity name (used for SQLite path templating)

    Returns:
        A configured (but not yet connected) DatabaseBackend

    Usage:
        db = create_backend({"backend": "sqlite", "sqlite": {"path": "data/test.db"}})
        await db.connect()
    """
    if config is None:
        config = DatabaseConfig()
    elif isinstance(config, dict):
        config = DatabaseConfig.from_dict(config)

    if config.backend == "sqlite":
        from overblick.core.database.sqlite_backend import SQLiteBackend
        return SQLiteBackend(config, identity)

    elif config.backend == "postgresql":
        from overblick.core.database.pg_backend import PostgreSQLBackend
        return PostgreSQLBackend(config, identity)

    else:
        raise ValueError(
            f"Unknown database backend: {config.backend!r}. "
            f"Supported: 'sqlite', 'postgresql'"
        )
