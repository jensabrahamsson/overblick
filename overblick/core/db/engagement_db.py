"""
Engagement database — unified engagement tracking.

Uses the async DatabaseBackend abstraction (SQLiteBackend or PostgreSQLBackend)
instead of raw sqlite3. All methods are async. Placeholder syntax adapts
automatically via ``db.ph(n)``.
"""

import logging
from typing import Any

from overblick.core.database.base import DatabaseBackend

logger = logging.getLogger(__name__)


class EngagementDB:
    """Async engagement tracker backed by a DatabaseBackend instance."""

    def __init__(self, db: DatabaseBackend, identity: str = ""):
        self._db = db
        self._identity = identity

    async def setup(self) -> None:
        """Create tables and indexes (idempotent)."""
        ph = self._db.ph

        await self._db.execute_script("""
            CREATE TABLE IF NOT EXISTS engagements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL,
                relevance_score REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT,
                title TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS processed_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT UNIQUE NOT NULL,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL,
                relevance_score REAL,
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS my_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT UNIQUE NOT NULL,
                title TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS my_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT UNIQUE NOT NULL,
                post_id TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reply_action_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT UNIQUE NOT NULL,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL,
                relevance_score REAL,
                retry_count INTEGER DEFAULT 0,
                last_attempt TEXT,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT DEFAULT (datetime(CURRENT_TIMESTAMP, '+2 days'))
            );

            CREATE TABLE IF NOT EXISTS challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge_id TEXT,
                question_raw TEXT,
                question_clean TEXT,
                answer TEXT,
                solver TEXT,
                correct INTEGER,
                endpoint TEXT,
                duration_ms REAL,
                http_status INTEGER,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_processed_replies_comment_id
                ON processed_replies(comment_id);
            CREATE INDEX IF NOT EXISTS idx_reply_queue_expires
                ON reply_action_queue(expires_at);
            CREATE INDEX IF NOT EXISTS idx_challenges_created
                ON challenges(created_at);
        """)

        logger.debug("EngagementDB schema initialized for '%s'", self._identity)

    # ------------------------------------------------------------------
    # Engagement tracking
    # ------------------------------------------------------------------

    async def record_engagement(self, post_id: str, action: str, score: float) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"INSERT INTO engagements (post_id, action, relevance_score) "
            f"VALUES ({ph(1)}, {ph(2)}, {ph(3)})",
            (post_id, action, score),
        )

    async def record_heartbeat(self, post_id: str, title: str) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"INSERT INTO heartbeats (post_id, title) VALUES ({ph(1)}, {ph(2)})",
            (post_id, title),
        )

    # ------------------------------------------------------------------
    # Reply processing
    # ------------------------------------------------------------------

    async def is_reply_processed(self, comment_id: str) -> bool:
        ph = self._db.ph
        row = await self._db.fetch_one(
            f"SELECT 1 FROM processed_replies WHERE comment_id = {ph(1)}",
            (comment_id,),
        )
        if row:
            return True
        row = await self._db.fetch_one(
            f"SELECT 1 FROM reply_action_queue WHERE comment_id = {ph(1)}",
            (comment_id,),
        )
        return row is not None

    async def mark_reply_processed(
        self, comment_id: str, post_id: str, action: str, score: float,
    ) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"INSERT OR IGNORE INTO processed_replies "
            f"(comment_id, post_id, action, relevance_score) "
            f"VALUES ({ph(1)}, {ph(2)}, {ph(3)}, {ph(4)})",
            (comment_id, post_id, action, score),
        )

    # ------------------------------------------------------------------
    # Reply action queue
    # ------------------------------------------------------------------

    async def queue_reply_action(
        self, comment_id: str, post_id: str, action: str, relevance_score: float,
    ) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"INSERT OR IGNORE INTO reply_action_queue "
            f"(comment_id, post_id, action, relevance_score) "
            f"VALUES ({ph(1)}, {ph(2)}, {ph(3)}, {ph(4)})",
            (comment_id, post_id, action, relevance_score),
        )

    async def get_pending_reply_actions(self, limit: int = 10) -> list[dict[str, Any]]:
        ph = self._db.ph
        rows = await self._db.fetch_all(
            f"SELECT id, comment_id, post_id, action, relevance_score, retry_count "
            f"FROM reply_action_queue "
            f"WHERE datetime(expires_at) > datetime('now') "
            f"ORDER BY created_at ASC LIMIT {ph(1)}",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "comment_id": r["comment_id"],
                "post_id": r["post_id"],
                "action": r["action"],
                "relevance_score": r["relevance_score"],
                "retry_count": r["retry_count"],
            }
            for r in rows
        ]

    async def remove_from_queue(self, queue_id: int) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"DELETE FROM reply_action_queue WHERE id = {ph(1)}",
            (queue_id,),
        )

    async def update_queue_retry(self, queue_id: int, error_msg: str) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"UPDATE reply_action_queue "
            f"SET retry_count = retry_count + 1, "
            f"last_attempt = CURRENT_TIMESTAMP, "
            f"error_message = {ph(1)} "
            f"WHERE id = {ph(2)}",
            (error_msg, queue_id),
        )

    async def cleanup_expired_queue_items(self) -> int:
        # Archive expired items into processed_replies, then delete.
        # Two separate execute() calls (each auto-commits). Acceptable
        # since INSERT OR IGNORE is idempotent and eventually consistent.
        await self._db.execute(
            "INSERT OR IGNORE INTO processed_replies "
            "(comment_id, post_id, action, relevance_score) "
            "SELECT comment_id, post_id, action || '_expired', relevance_score "
            "FROM reply_action_queue WHERE datetime(expires_at) <= datetime('now')"
        )
        return await self._db.execute(
            "DELETE FROM reply_action_queue WHERE datetime(expires_at) <= datetime('now')"
        )

    async def trim_stale_queue_items(self, max_age_hours: int = 12) -> int:
        ph = self._db.ph
        age_param = f"-{max_age_hours}"
        await self._db.execute(
            f"INSERT OR IGNORE INTO processed_replies "
            f"(comment_id, post_id, action, relevance_score) "
            f"SELECT comment_id, post_id, action || '_stale', relevance_score "
            f"FROM reply_action_queue "
            f"WHERE datetime(created_at) <= datetime('now', {ph(1)} || ' hours')",
            (age_param,),
        )
        return await self._db.execute(
            f"DELETE FROM reply_action_queue "
            f"WHERE datetime(created_at) <= datetime('now', {ph(1)} || ' hours')",
            (age_param,),
        )

    # ------------------------------------------------------------------
    # My posts / comments tracking
    # ------------------------------------------------------------------

    async def track_my_post(self, post_id: str, title: str) -> None:
        if not post_id:
            return
        ph = self._db.ph
        await self._db.execute(
            f"INSERT OR IGNORE INTO my_posts (post_id, title) VALUES ({ph(1)}, {ph(2)})",
            (post_id, title),
        )

    async def track_my_comment(self, comment_id: str, post_id: str) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"INSERT OR IGNORE INTO my_comments (comment_id, post_id) VALUES ({ph(1)}, {ph(2)})",
            (comment_id, post_id),
        )

    async def untrack_my_post(self, post_id: str) -> None:
        ph = self._db.ph
        await self._db.execute(
            f"DELETE FROM my_posts WHERE post_id = {ph(1)}",
            (post_id,),
        )

    async def get_my_post_ids(self, limit: int = 10) -> list[str]:
        ph = self._db.ph
        rows = await self._db.fetch_all(
            f"SELECT post_id FROM my_posts WHERE post_id != '' ORDER BY created_at DESC LIMIT {ph(1)}",
            (limit,),
        )
        return [r["post_id"] for r in rows]

    # ------------------------------------------------------------------
    # Challenge tracking
    # ------------------------------------------------------------------

    async def record_challenge(
        self,
        challenge_id: str | None,
        question_raw: str | None,
        question_clean: str | None,
        answer: str | None,
        solver: str | None,
        correct: bool,
        endpoint: str | None,
        duration_ms: float,
        http_status: int | None = None,
        error: str | None = None,
    ) -> None:
        """Record a challenge attempt for analysis."""
        ph = self._db.ph
        await self._db.execute(
            f"INSERT INTO challenges "
            f"(challenge_id, question_raw, question_clean, answer, solver, "
            f"correct, endpoint, duration_ms, http_status, error) "
            f"VALUES ({ph(1)}, {ph(2)}, {ph(3)}, {ph(4)}, {ph(5)}, "
            f"{ph(6)}, {ph(7)}, {ph(8)}, {ph(9)}, {ph(10)})",
            (
                challenge_id, question_raw, question_clean, answer, solver,
                1 if correct else 0, endpoint, duration_ms, http_status, error,
            ),
        )

    async def get_recent_challenges(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent challenge attempts for analysis."""
        ph = self._db.ph
        rows = await self._db.fetch_all(
            f"SELECT id, challenge_id, question_raw, question_clean, answer, "
            f"solver, correct, endpoint, duration_ms, http_status, error, created_at "
            f"FROM challenges ORDER BY created_at DESC LIMIT {ph(1)}",
            (limit,),
        )
        return [dict(r) for r in rows]
