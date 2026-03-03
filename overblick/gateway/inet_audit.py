"""
Dedicated audit log for the Internet Gateway.

Every proxied request, authentication failure, rate limit hit, and
security violation is logged to an append-only SQLite database.

Follows the same pattern as overblick.core.security.audit_log:
- SQLite WAL mode for concurrent reads
- ThreadPoolExecutor for non-blocking async writes
- Background cleanup with configurable retention
"""

import asyncio
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inet_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    key_id TEXT NOT NULL DEFAULT '',
    key_name TEXT NOT NULL DEFAULT '',
    source_ip TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL DEFAULT 0,
    request_tokens INTEGER NOT NULL DEFAULT 0,
    response_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0.0,
    error TEXT NOT NULL DEFAULT '',
    violation TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_inet_audit_timestamp ON inet_audit(timestamp);
CREATE INDEX IF NOT EXISTS idx_inet_audit_key_id ON inet_audit(key_id);
CREATE INDEX IF NOT EXISTS idx_inet_audit_source_ip ON inet_audit(source_ip);
CREATE INDEX IF NOT EXISTS idx_inet_audit_violation ON inet_audit(violation);
"""


class InetAuditLog:
    """Audit logger for Internet Gateway requests.

    Thread-safe, non-blocking writes via ThreadPoolExecutor.
    Automatic retention cleanup (default: 90 days).

    Usage:
        audit = InetAuditLog(Path("data/internet_gateway/audit.db"))
        audit.log(key_id="abc123", source_ip="1.2.3.4", method="POST",
                  path="/v1/chat/completions", status_code=200, latency_ms=1500)
    """

    _DEFAULT_RETENTION_DAYS = 90
    _CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour

    def __init__(self, db_path: Path, retention_days: int = _DEFAULT_RETENTION_DAYS):
        self._db_path = db_path
        self._retention_days = retention_days
        self._cleanup_task: asyncio.Task | None = None
        self._conn: sqlite3.Connection | None = None
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
        assert self._conn is not None
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._write_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="inet-audit-write",
        )

    def _log_sync(
        self,
        key_id: str,
        key_name: str,
        source_ip: str,
        method: str,
        path: str,
        model: str,
        status_code: int,
        request_tokens: int,
        response_tokens: int,
        latency_ms: float,
        error: str,
        violation: str,
    ) -> int:
        """Synchronous log write (runs in executor thread)."""
        assert self._conn is not None
        conn = self._conn
        cursor = conn.execute(
            """
            INSERT INTO inet_audit
                (timestamp, key_id, key_name, source_ip, method, path,
                 model, status_code, request_tokens, response_tokens,
                 latency_ms, error, violation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                key_id,
                key_name,
                source_ip,
                method,
                path,
                model,
                status_code,
                request_tokens,
                response_tokens,
                latency_ms,
                error,
                violation,
            ),
        )
        conn.commit()
        assert cursor.lastrowid is not None
        return cast(int, cursor.lastrowid)

    def log(
        self,
        key_id: str = "",
        key_name: str = "",
        source_ip: str = "",
        method: str = "",
        path: str = "",
        model: str = "",
        status_code: int = 0,
        request_tokens: int = 0,
        response_tokens: int = 0,
        latency_ms: float = 0.0,
        error: str = "",
        violation: str = "",
    ) -> None:
        """Log a request (fire-and-forget, non-blocking in async context)."""
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                self._write_executor,
                self._log_sync,
                key_id,
                key_name,
                source_ip,
                method,
                path,
                model,
                status_code,
                request_tokens,
                response_tokens,
                latency_ms,
                error,
                violation,
            )
        except RuntimeError:
            # No event loop — write synchronously
            self._log_sync(
                key_id,
                key_name,
                source_ip,
                method,
                path,
                model,
                status_code,
                request_tokens,
                response_tokens,
                latency_ms,
                error,
                violation,
            )

    def query(
        self,
        key_id: str | None = None,
        source_ip: str | None = None,
        violation: str | None = None,
        since: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit entries with optional filters."""
        assert self._conn is not None
        conn = self._conn
        conditions: list[str] = []
        params: list[Any] = []

        if key_id:
            conditions.append("key_id = ?")
            params.append(key_id)
        if source_ip:
            conditions.append("source_ip = ?")
            params.append(source_ip)
        if violation:
            conditions.append("violation = ?")
            params.append(violation)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])

        cursor = conn.execute(
            f"""
            SELECT id, timestamp, key_id, key_name, source_ip, method, path,
                   model, status_code, request_tokens, response_tokens,
                   latency_ms, error, violation
            FROM inet_audit
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def count_violations(self, source_ip: str, since: float) -> int:
        """Count violations from an IP since a given timestamp."""
        assert self._conn is not None
        conn = self._conn
        cursor = conn.execute(
            """
            SELECT COUNT(*) FROM inet_audit
            WHERE source_ip = ? AND violation != '' AND timestamp >= ?
            """,
            (source_ip, since),
        )
        return cursor.fetchone()[0]

    def _trim_old_entries(self) -> int:
        """Remove entries older than the retention period."""
        assert self._conn is not None
        conn = self._conn
        cutoff = time.time() - (self._retention_days * 86400)
        cursor = conn.execute(
            "DELETE FROM inet_audit WHERE timestamp < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        if deleted > 0:
            conn.commit()
            logger.info(
                "Internet gateway audit: trimmed %d entries older than %d days",
                deleted,
                self._retention_days,
            )
        return deleted

    def start_background_cleanup(self) -> None:
        """Start periodic background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def stop_background_cleanup(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Periodically trim old entries."""
        try:
            while True:
                await asyncio.sleep(self._CLEANUP_INTERVAL_SECONDS)
                try:
                    self._trim_old_entries()
                except Exception as e:
                    logger.warning("Audit cleanup error: %s", e)
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        """Close the database connection and stop background tasks."""
        self.stop_background_cleanup()
        self._write_executor.shutdown(wait=True)
        if self._conn:
            self._conn.close()
            self._conn = None
