"""
Persistent ban storage for ViolationTracker using SQLite.

Stores banned IPs with expiry timestamps. Violation counts remain in-memory
for performance, but bans survive restarts.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, cast

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS banned_ips (
    ip TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_banned_ips_expires ON banned_ips(expires_at);
"""


class SQLiteBanStore:
    """SQLite-backed storage for IP bans."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

        # Initialize schema
        with self._lock:
            conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=True)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=True)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return cast(sqlite3.Connection, self._local.conn)

    def add_ban(self, ip: str, expires_at: float) -> None:
        """Record a banned IP with expiry timestamp."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO banned_ips (ip, expires_at, created_at) VALUES (?, ?, ?)",
            (ip, expires_at, time.time()),
        )
        conn.commit()
        logger.debug("Persisted ban for IP %s (expires at %s)", ip, expires_at)

    def remove_ban(self, ip: str) -> None:
        """Remove a ban (e.g., after expiry)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM banned_ips WHERE ip = ?", (ip,))
        conn.commit()
        logger.debug("Removed ban for IP %s", ip)

    def load_bans(self) -> dict[str, float]:
        """Load all active bans from disk.

        Returns:
            Dict mapping IP -> expiry timestamp.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT ip, expires_at FROM banned_ips WHERE expires_at > ?",
            (time.time(),),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def cleanup_expired(self) -> int:
        """Delete expired bans from the database.

        Returns:
            Number of rows deleted.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM banned_ips WHERE expires_at <= ?",
            (time.time(),),
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.debug("Cleaned up %d expired bans from database", deleted)
        return deleted

    def close(self) -> None:
        """Close all database connections."""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        if hasattr(self._local, "conn"):
            del self._local.conn
