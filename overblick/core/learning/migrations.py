"""
SQLite schema migrations for the learning system.

Creates the identity_learnings table with embedding BLOB support.
"""

import logging

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identity_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    source TEXT DEFAULT '',
    source_context TEXT DEFAULT '',
    status TEXT DEFAULT 'candidate',
    review_reason TEXT DEFAULT '',
    confidence REAL DEFAULT 0.5,
    embedding BLOB DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_learnings_status ON identity_learnings(status);
"""


async def run_migrations(db_path) -> None:
    """Run learning system migrations on the given SQLite database."""
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
    logger.info("Learning migrations applied to %s", db_path)
