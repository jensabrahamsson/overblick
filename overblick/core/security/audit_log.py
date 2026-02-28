"""
Structured action audit logging (SQLite).

Every significant action (API call, LLM request, engagement, challenge)
is logged to an append-only SQLite database per identity.

Thread-safety: all writes go through a dedicated single-thread executor
so the async event loop is never blocked by SQLite disk I/O.
"""

import asyncio
import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
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

    Thread-safe (SQLite handles locking). Append-only with automatic
    rotation: entries older than retention_days are trimmed periodically.

    Writes are offloaded to a background thread via ThreadPoolExecutor
    to avoid blocking the async event loop.

    Usage:
        audit = AuditLog(Path("data/anomal/audit.db"), identity="anomal")
        audit.log("api_call", category="moltbook", details={"endpoint": "/posts"})
    """

    _DEFAULT_RETENTION_DAYS = 90
    _CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour

    def __init__(
        self,
        db_path: Path,
        identity: str,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ):
        self._db_path = db_path
        self._identity = identity
        self._retention_days = retention_days
        self._cleanup_task: Optional[asyncio.Task] = None
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Single-thread executor for non-blocking writes
        self._write_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="audit-write",
        )

    def _log_sync(
        self,
        action: str,
        category: str,
        plugin: Optional[str],
        details_json: Optional[str],
        success: bool,
        duration_ms: Optional[float],
        error: Optional[str],
    ) -> int:
        """Synchronous log write (runs in executor thread)."""
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
        Log an action (fire-and-forget, non-blocking when event loop is running).

        If called from an async context, the write is offloaded to a background
        thread so the event loop is not blocked by SQLite disk I/O. If called
        from a sync context (e.g. during setup/teardown), it writes directly.

        Args:
            action: Action name (e.g. "api_call", "llm_request", "challenge_solved")
            category: Category (e.g. "moltbook", "security", "llm")
            plugin: Plugin name that performed the action
            details: JSON-serializable details
            success: Whether the action succeeded
            duration_ms: How long the action took
            error: Error message if failed

        Returns:
            Row ID of the log entry (0 if async submission)
        """
        details_json = json.dumps(details) if details else None

        # Try to offload to executor if an event loop is running
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                self._write_executor,
                self._log_sync,
                action, category, plugin, details_json,
                success, duration_ms, error,
            )
            return 0  # ID not available for async writes
        except RuntimeError:
            # No event loop running — write synchronously (setup/teardown)
            return self._log_sync(
                action, category, plugin, details_json,
                success, duration_ms, error,
            )

    def query(
        self,
        action: Optional[str] = None,
        category: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Query audit log entries with pagination.

        Args:
            action: Filter by action name
            category: Filter by category
            since: Filter entries after this timestamp
            limit: Max entries to return
            offset: Number of entries to skip (for pagination)

        Returns:
            List of log entry dicts
        """
        conditions = []
        params: list[Any] = []

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
        params.extend([limit, offset])

        cursor = self._conn.execute(
            f"""
            SELECT id, timestamp, action, category, identity, plugin,
                   details, success, duration_ms, error
            FROM audit_log
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
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

    def _trim_old_entries(self) -> int:
        """
        Remove audit entries older than the retention period.

        Returns:
            Number of entries deleted
        """
        cutoff = time.time() - (self._retention_days * 86400)
        cursor = self._conn.execute(
            "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        if deleted > 0:
            self._conn.commit()
            logger.info(
                "Audit log trimmed: %d entries older than %d days removed",
                deleted,
                self._retention_days,
            )
        return deleted

    def start_background_cleanup(self) -> None:
        """Start the periodic background cleanup task.

        Call this after the event loop is running (e.g. during orchestrator
        startup). Safe to call multiple times — only one task runs at a time.
        """
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("Audit log background cleanup started (every %ds)",
                      self._CLEANUP_INTERVAL_SECONDS)

    def stop_background_cleanup(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Periodically trim old entries in the background."""
        try:
            while True:
                await asyncio.sleep(self._CLEANUP_INTERVAL_SECONDS)
                try:
                    deleted = self._trim_old_entries()
                    if deleted:
                        logger.debug("Background cleanup trimmed %d entries", deleted)
                except Exception as e:
                    logger.warning("Background audit cleanup error: %s", e)
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        """Close the database connection and stop background tasks."""
        self.stop_background_cleanup()
        # Wait for pending writes to complete
        self._write_executor.shutdown(wait=True)
        if self._conn:
            self._conn.close()
            self._conn = None
