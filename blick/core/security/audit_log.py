"""
Structured action audit logging (SQLite).

Every significant action (API call, LLM request, engagement, challenge)
is logged to an append-only SQLite database per identity.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    action TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    identity TEXT NOT NULL,
    plugin TEXT,
    details TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    duration_ms REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category);
"""


class AuditLog:
    """
    Structured audit logger backed by SQLite.

    Thread-safe (SQLite handles locking). Append-only — no updates or deletes.

    Usage:
        audit = AuditLog(Path("data/anomal/audit.db"), identity="anomal")
        audit.log("api_call", category="moltbook", details={"endpoint": "/posts"})
    """

    def __init__(self, db_path: Path, identity: str):
        self._db_path = db_path
        self._identity = identity
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def log(
        self,
        action: str,
        category: str = "general",
        plugin: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        success: bool = True,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> int:
        """
        Log an action.

        Args:
            action: Action name (e.g. "api_call", "llm_request", "challenge_solved")
            category: Category (e.g. "moltbook", "security", "llm")
            plugin: Plugin name that performed the action
            details: JSON-serializable details
            success: Whether the action succeeded
            duration_ms: How long the action took
            error: Error message if failed

        Returns:
            Row ID of the log entry
        """
        details_json = json.dumps(details) if details else None

        cursor = self._conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, action, category, identity, plugin, details, success, duration_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                action,
                category,
                self._identity,
                plugin,
                details_json,
                1 if success else 0,
                duration_ms,
                error,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def query(
        self,
        action: Optional[str] = None,
        category: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query audit log entries.

        Args:
            action: Filter by action name
            category: Filter by category
            since: Filter entries after this timestamp
            limit: Max entries to return

        Returns:
            List of log entry dicts
        """
        conditions = []
        params = []

        if action:
            conditions.append("action = ?")
            params.append(action)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        cursor = self._conn.execute(
            f"""
            SELECT id, timestamp, action, category, identity, plugin,
                   details, success, duration_ms, error
            FROM audit_log
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params,
        )

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        results = []
        for row in rows:
            entry = dict(zip(columns, row))
            if entry["details"]:
                try:
                    entry["details"] = json.loads(entry["details"])
                except json.JSONDecodeError:
                    pass
            entry["success"] = bool(entry["success"])
            results.append(entry)

        return results

    def count(
        self,
        action: Optional[str] = None,
        since: Optional[float] = None,
    ) -> int:
        """Count log entries matching criteria."""
        conditions = []
        params = []

        if action:
            conditions.append("action = ?")
            params.append(action)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor = self._conn.execute(
            f"SELECT COUNT(*) FROM audit_log {where}",
            params,
        )
        return cursor.fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
