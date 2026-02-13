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
            logger.error("Failed to open audit DB for '%s': %s", identity, e)
            return None

    def query(
        self,
        identity: str = "",
        category: str = "",
        action: str = "",
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
                logger.error("Error querying audit for '%s': %s", ident, e)

        # Sort all results by timestamp descending, limit total
        results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return results[:limit]

    def count(self, identity: str = "", since_hours: int = 24) -> int:
        """Count audit entries for an identity (or all)."""
        since = time.time() - (since_hours * 3600)
        total = 0

        identities = [identity] if identity else self._discover_identities()

        for ident in identities:
            conn = self._get_connection(ident)
            if not conn:
                continue
            try:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE timestamp >= ?",
                    (since,),
                )
                total += cursor.fetchone()[0]
            except Exception as e:
                logger.error("Error counting audit for '%s': %s", ident, e)

        return total

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

    def _discover_identities(self) -> list[str]:
        """Find identities that have audit databases."""
        if not self._data_dir.exists():
            return []
        return sorted(
            d.name
            for d in self._data_dir.iterdir()
            if d.is_dir() and (d / "audit.db").exists()
        )

    def close(self) -> None:
        """Close all database connections."""
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()
