"""
Audit service — read-only access to audit databases.

Security: Opens SQLite databases in read-only mode (?mode=ro)
to physically prevent accidental writes from the dashboard.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditService:
    """Read-only access to identity audit databases."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._data_dir = base_dir / "data"
        self._connections: dict[str, sqlite3.Connection] = {}

    def _get_connection(self, identity: str) -> Optional[sqlite3.Connection]:
        """Get or create a read-only connection to an identity's audit DB."""
        if identity in self._connections:
            return self._connections[identity]

        db_path = self._data_dir / identity / "audit.db"
        if not db_path.exists():
            return None

        try:
            # Open in read-only mode (security: prevents accidental writes)
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            self._connections[identity] = conn
            return conn
        except Exception as e:
            logger.error("Failed to open audit DB for '%s': %s", identity, e, exc_info=True)
            return None

    def query(
        self,
        identity: str = "",
        category: str = "",
        action: str = "",
        plugin: str = "",
        since_hours: int = 24,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Query audit entries, optionally across all identities.

        Args:
            identity: Filter by identity (empty = all identities)
            category: Filter by category
            action: Filter by action
            since_hours: Time window in hours
            limit: Max entries to return

        Returns:
            List of audit entry dicts, sorted by timestamp descending
        """
        since = time.time() - (since_hours * 3600)
        results = []

        # Determine which identities to query
        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue

            try:
                conditions = ["timestamp >= ?"]
                params: list[Any] = [since]

                if category:
                    conditions.append("category = ?")
                    params.append(category)
                if action:
                    conditions.append("action = ?")
                    params.append(action)
                if plugin:
                    conditions.append("plugin = ?")
                    params.append(plugin)

                # SECURITY: All condition strings are hardcoded literals.
                # User input ONLY goes through the params list (parameterized).
                where = " AND ".join(conditions)
                params.append(limit)

                cursor = conn.execute(
                    f"""
                    SELECT id, timestamp, action, category, identity, plugin,
                           details, success, duration_ms, error
                    FROM audit_log
                    WHERE {where}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params,
                )

                for row in cursor.fetchall():
                    entry = dict(row)
                    if entry.get("details"):
                        try:
                            entry["details"] = json.loads(entry["details"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    entry["success"] = bool(entry.get("success", 1))
                    results.append(entry)

            except Exception as e:
                logger.error("Error querying audit for '%s': %s", ident, e, exc_info=True)

        # Sort all results by timestamp descending, limit total
        results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return results[:limit]

    def count(
        self,
        identity: str = "",
        since_hours: int = 24,
        category: str = "",
        success: Optional[bool] = None,
    ) -> int:
        """Count audit entries with optional category/success filters."""
        since = time.time() - (since_hours * 3600)
        total = 0

        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                conditions = ["timestamp >= ?"]
                params: list[Any] = [since]

                if category:
                    conditions.append("category = ?")
                    params.append(category)
                if success is not None:
                    conditions.append("success = ?")
                    params.append(1 if success else 0)

                where = " AND ".join(conditions)
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM audit_log WHERE {where}",
                    params,
                )
                total += cursor.fetchone()[0]
            except Exception as e:
                logger.error("Error counting audit for '%s': %s", ident, e, exc_info=True)

        return total

    def count_with_failures(
        self,
        identity: str = "",
        since_hours: int = 24,
        category: str = "",
    ) -> tuple[int, int]:
        """Count total entries and failures in a single query per identity.

        Returns:
            Tuple of (total_count, failure_count).
        """
        since = time.time() - (since_hours * 3600)
        total = 0
        failures = 0

        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                conditions = ["timestamp >= ?"]
                params: list[Any] = [since]
                if category:
                    conditions.append("category = ?")
                    params.append(category)

                where = " AND ".join(conditions)
                cursor = conn.execute(
                    f"SELECT COUNT(*), SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) "
                    f"FROM audit_log WHERE {where}",
                    params,
                )
                row = cursor.fetchone()
                total += row[0] or 0
                failures += row[1] or 0
            except Exception as e:
                logger.error("Error counting audit for '%s': %s", ident, e, exc_info=True)

        return total, failures

    def count_by_hour(
        self,
        hours: int = 12,
        identity: str = "",
        category: str = "",
    ) -> list[dict[str, Any]]:
        """Aggregate audit events by hour bucket.

        Returns a list of dicts sorted chronologically:
            [{"hour": "14:00", "total": 42, "failures": 3}, ...]
        """
        now = time.time()
        since = now - (hours * 3600)
        # Bucket map: hour_offset -> {total, failures}
        buckets: dict[int, dict[str, int]] = {
            h: {"total": 0, "failures": 0} for h in range(hours)
        }

        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                conditions = ["timestamp >= ?"]
                params: list[Any] = [since]

                if category:
                    conditions.append("category = ?")
                    params.append(category)

                where = " AND ".join(conditions)
                cursor = conn.execute(
                    f"""
                    SELECT
                        CAST(({now} - timestamp) / 3600 AS INTEGER) AS bucket,
                        COUNT(*) AS cnt,
                        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fails
                    FROM audit_log
                    WHERE {where}
                    GROUP BY bucket
                    """,
                    params,
                )
                for row in cursor.fetchall():
                    bucket_idx = row[0]
                    if 0 <= bucket_idx < hours:
                        buckets[bucket_idx]["total"] += row[1]
                        buckets[bucket_idx]["failures"] += row[2]
            except Exception as e:
                logger.error("Error in count_by_hour for '%s': %s", ident, e, exc_info=True)

        # Convert to list, sorted oldest-first (highest offset = oldest)
        import datetime
        result = []
        for offset in reversed(range(hours)):
            ts = now - (offset * 3600)
            hour_label = datetime.datetime.fromtimestamp(ts).strftime("%H:00")
            result.append({
                "hour": hour_label,
                "total": buckets[offset]["total"],
                "failures": buckets[offset]["failures"],
            })
        return result

    def count_by_category(
        self,
        since_hours: int = 24,
        identity: str = "",
    ) -> dict[str, int]:
        """Count audit events grouped by category.

        Returns: {"llm": 150, "moltbook": 80, "security": 12, ...}
        """
        since = time.time() - (since_hours * 3600)
        totals: dict[str, int] = {}

        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                conditions = ["timestamp >= ?"]
                params: list[Any] = [since]

                where = " AND ".join(conditions)
                cursor = conn.execute(
                    f"SELECT category, COUNT(*) FROM audit_log WHERE {where} GROUP BY category",
                    params,
                )
                for row in cursor.fetchall():
                    cat = row[0] or "unknown"
                    totals[cat] = totals.get(cat, 0) + row[1]
            except Exception as e:
                logger.error("Error in count_by_category for '%s': %s", ident, e, exc_info=True)

        return totals

    def get_categories(self) -> list[str]:
        """Get all distinct categories across all audit databases."""
        categories = set()
        for ident in self._discover_identities():
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                cursor = conn.execute("SELECT DISTINCT category FROM audit_log")
                for row in cursor.fetchall():
                    categories.add(row[0])
            except Exception:
                pass
        return sorted(categories)

    def get_actions(self) -> list[str]:
        """Get all distinct actions across all audit databases."""
        actions = set()
        for ident in self._discover_identities():
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                cursor = conn.execute("SELECT DISTINCT action FROM audit_log")
                for row in cursor.fetchall():
                    actions.add(row[0])
            except Exception:
                pass
        return sorted(actions)

    # Cache TTL for identity discovery (seconds)
    _IDENTITY_CACHE_TTL = 30.0

    def _discover_identities(self) -> list[str]:
        """Find identities that have audit databases (cached with TTL)."""
        now = time.time()
        if (
            hasattr(self, "_identity_cache")
            and hasattr(self, "_identity_cache_ts")
            and now - self._identity_cache_ts < self._IDENTITY_CACHE_TTL
        ):
            return self._identity_cache

        if not self._data_dir.exists():
            self._identity_cache: list[str] = []
            self._identity_cache_ts: float = now
            return self._identity_cache

        self._identity_cache = sorted(
            d.name
            for d in self._data_dir.iterdir()
            if d.is_dir() and (d / "audit.db").exists()
        )
        self._identity_cache_ts = now
        return self._identity_cache

    def close(self) -> None:
        """Close all database connections."""
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()
