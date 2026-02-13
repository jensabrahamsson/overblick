"""
Engagement database — unified engagement tracking.

Ported from anomal_moltbook/core/engagement_db.py.
Identical schema, used by all identities with isolated DB files.
"""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class EngagementDB:
    """SQLite database for engagement tracking (per-identity isolated)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.debug(f"EngagementDB initialized at {self.db_path}")

    @contextmanager
    def _get_connection(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS engagements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    relevance_score REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT,
                    title TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT UNIQUE NOT NULL,
                    post_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    relevance_score REAL,
                    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS my_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT UNIQUE NOT NULL,
                    title TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS my_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT UNIQUE NOT NULL,
                    post_id TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
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
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_replies_comment_id
                ON processed_replies(comment_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reply_queue_expires
                ON reply_action_queue(expires_at)
            """)

    def record_engagement(self, post_id: str, action: str, score: float) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO engagements (post_id, action, relevance_score) VALUES (?, ?, ?)",
                (post_id, action, score),
            )

    def record_heartbeat(self, post_id: str, title: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO heartbeats (post_id, title) VALUES (?, ?)",
                (post_id, title),
            )

    def is_reply_processed(self, comment_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM processed_replies WHERE comment_id = ?", (comment_id,))
            if cursor.fetchone():
                return True
            cursor.execute("SELECT 1 FROM reply_action_queue WHERE comment_id = ?", (comment_id,))
            return cursor.fetchone() is not None

    def mark_reply_processed(self, comment_id: str, post_id: str, action: str, score: float) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_replies (comment_id, post_id, action, relevance_score) VALUES (?, ?, ?, ?)",
                (comment_id, post_id, action, score),
            )

    def queue_reply_action(self, comment_id: str, post_id: str, action: str, relevance_score: float) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO reply_action_queue (comment_id, post_id, action, relevance_score) VALUES (?, ?, ?, ?)",
                (comment_id, post_id, action, relevance_score),
            )

    def get_pending_reply_actions(self, limit: int = 10) -> list[dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, comment_id, post_id, action, relevance_score, retry_count
                   FROM reply_action_queue
                   WHERE datetime(expires_at) > datetime('now')
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            )
            return [
                {"id": r[0], "comment_id": r[1], "post_id": r[2],
                 "action": r[3], "relevance_score": r[4], "retry_count": r[5]}
                for r in cursor.fetchall()
            ]

    def remove_from_queue(self, queue_id: int) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM reply_action_queue WHERE id = ?", (queue_id,))

    def update_queue_retry(self, queue_id: int, error_msg: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE reply_action_queue SET retry_count = retry_count + 1, last_attempt = CURRENT_TIMESTAMP, error_message = ? WHERE id = ?",
                (error_msg, queue_id),
            )

    def cleanup_expired_queue_items(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO processed_replies (comment_id, post_id, action, relevance_score)
                   SELECT comment_id, post_id, action || '_expired', relevance_score
                   FROM reply_action_queue WHERE datetime(expires_at) <= datetime('now')"""
            )
            cursor.execute("DELETE FROM reply_action_queue WHERE datetime(expires_at) <= datetime('now')")
            return cursor.rowcount

    def trim_stale_queue_items(self, max_age_hours: int = 12) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO processed_replies (comment_id, post_id, action, relevance_score)
                   SELECT comment_id, post_id, action || '_stale', relevance_score
                   FROM reply_action_queue WHERE datetime(created_at) <= datetime('now', ? || ' hours')""",
                (f"-{max_age_hours}",),
            )
            cursor.execute(
                "DELETE FROM reply_action_queue WHERE datetime(created_at) <= datetime('now', ? || ' hours')",
                (f"-{max_age_hours}",),
            )
            return cursor.rowcount

    def track_my_post(self, post_id: str, title: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO my_posts (post_id, title) VALUES (?, ?)", (post_id, title))

    def track_my_comment(self, comment_id: str, post_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO my_comments (comment_id, post_id) VALUES (?, ?)", (comment_id, post_id))

    def untrack_my_post(self, post_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM my_posts WHERE post_id = ?", (post_id,))

    def get_my_post_ids(self, limit: int = 10) -> list[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT post_id FROM my_posts ORDER BY created_at DESC LIMIT ?", (limit,))
            return [row[0] for row in cursor.fetchall()]
