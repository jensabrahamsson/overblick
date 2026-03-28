"""
Token usage logger for the LLM Gateway.

Persists prompt/completion/total token counts per request to SQLite,
with time-range queries and CSV export for reporting.
"""

import csv
import io
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    backend TEXT,
    model TEXT,
    identity_hint TEXT,
    priority TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    response_time_ms REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_backend ON token_usage(backend);
"""

_PERIOD_MAP = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


class TokenLogger:
    """SQLite-backed token usage logger with query and CSV export."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("TokenLogger initialized: %s", self._db_path)

    def record(
        self,
        backend: str = "",
        model: str = "",
        identity_hint: str = "",
        priority: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        response_time_ms: float = 0.0,
    ) -> None:
        """Record a single request's token usage."""
        self._conn.execute(
            """INSERT INTO token_usage
               (timestamp, backend, model, identity_hint, priority,
                prompt_tokens, completion_tokens, total_tokens, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                backend,
                model,
                identity_hint,
                priority,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                response_time_ms,
            ),
        )
        self._conn.commit()

    def query(
        self,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
        backend: str | None = None,
    ) -> list[dict]:
        """Query token usage rows with optional filters."""
        conditions = []
        params: list = []

        if period and period in _PERIOD_MAP:
            cutoff = (datetime.now() - _PERIOD_MAP[period]).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        if start:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end:
            conditions.append("timestamp <= ?")
            params.append(end)
        if backend:
            conditions.append("backend = ?")
            params.append(backend)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM token_usage {where} ORDER BY timestamp DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def summary(
        self,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """Aggregated token usage summary."""
        conditions = []
        params: list = []

        if period and period in _PERIOD_MAP:
            cutoff = (datetime.now() - _PERIOD_MAP[period]).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        if start:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end:
            conditions.append("timestamp <= ?")
            params.append(end)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Total
        row = self._conn.execute(
            f"""SELECT
                COUNT(*) as requests,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(AVG(response_time_ms), 0) as avg_response_ms
            FROM token_usage {where}""",
            params,
        ).fetchone()

        # Per backend
        backend_rows = self._conn.execute(
            f"""SELECT backend,
                COUNT(*) as requests,
                SUM(total_tokens) as total_tokens,
                AVG(response_time_ms) as avg_response_ms
            FROM token_usage {where}
            GROUP BY backend ORDER BY total_tokens DESC""",
            params,
        ).fetchall()

        # Per identity
        identity_rows = self._conn.execute(
            f"""SELECT identity_hint,
                COUNT(*) as requests,
                SUM(total_tokens) as total_tokens
            FROM token_usage {where}
            GROUP BY identity_hint ORDER BY total_tokens DESC""",
            params,
        ).fetchall()

        return {
            "period": period or f"{start or 'all'} to {end or 'now'}",
            "total": dict(row),
            "per_backend": [dict(r) for r in backend_rows],
            "per_identity": [dict(r) for r in identity_rows],
        }

    @staticmethod
    def to_csv(rows: list[dict]) -> str:
        """Convert query rows to CSV string."""
        if not rows:
            return "No data\n"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
