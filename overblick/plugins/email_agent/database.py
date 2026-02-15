"""
Database layer for the email agent plugin.

Wraps DatabaseBackend (SQLiteBackend) with email-specific tables
for classification records, agent learnings, and goals. Uses the
framework's migration system for schema management.
"""

import logging
from typing import Optional

from overblick.core.database.base import DatabaseBackend, Migration, MigrationManager
from overblick.plugins.email_agent.models import (
    AgentGoal,
    AgentLearning,
    EmailRecord,
)

logger = logging.getLogger(__name__)

# Schema migrations
MIGRATIONS = [
    Migration(
        version=1,
        name="email_records",
        up_sql="""
            CREATE TABLE IF NOT EXISTS email_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_from TEXT NOT NULL,
                email_subject TEXT NOT NULL,
                email_snippet TEXT DEFAULT '',
                classified_intent TEXT NOT NULL,
                confidence REAL NOT NULL,
                reasoning TEXT DEFAULT '',
                action_taken TEXT DEFAULT '',
                boss_feedback TEXT,
                was_correct BOOLEAN,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS email_records;",
    ),
    Migration(
        version=2,
        name="agent_learnings",
        up_sql="""
            CREATE TABLE IF NOT EXISTS agent_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learning_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                email_from TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS agent_learnings;",
    ),
    Migration(
        version=3,
        name="agent_goals",
        up_sql="""
            CREATE TABLE IF NOT EXISTS agent_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                priority INTEGER DEFAULT 50,
                progress REAL DEFAULT 0.0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS agent_goals;",
    ),
    Migration(
        version=4,
        name="notification_tracking",
        up_sql="""
            CREATE TABLE IF NOT EXISTS notification_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_record_id INTEGER NOT NULL,
                tg_message_id INTEGER NOT NULL,
                tg_chat_id TEXT NOT NULL,
                notification_text TEXT DEFAULT '',
                feedback_received BOOLEAN DEFAULT FALSE,
                feedback_text TEXT,
                feedback_sentiment TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS notification_tracking;",
    ),
]


class EmailAgentDB:
    """Database layer for the email agent — wraps DatabaseBackend."""

    def __init__(self, db: DatabaseBackend):
        self._db = db
        self._migrations = MigrationManager(db)

    async def setup(self) -> None:
        """Connect and apply migrations."""
        await self._db.connect()
        await self._migrations.apply(MIGRATIONS)

    # -- Email records --

    async def record_email(self, record: EmailRecord) -> int:
        """Record a processed email classification."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO email_records "
            "(email_from, email_subject, email_snippet, classified_intent, "
            "confidence, reasoning, action_taken) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.email_from,
                record.email_subject,
                record.email_snippet,
                record.classified_intent,
                record.confidence,
                record.reasoning,
                record.action_taken,
            ),
        )
        return row_id or 0

    async def update_action_taken(self, record_id: int, action_taken: str) -> None:
        """Update the action_taken field on an email record."""
        await self._db.execute(
            "UPDATE email_records SET action_taken = ? WHERE id = ?",
            (action_taken, record_id),
        )

    async def get_recent_emails(self, limit: int = 20) -> list[EmailRecord]:
        """Get most recent email records."""
        rows = await self._db.fetch_all(
            "SELECT * FROM email_records ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [
            EmailRecord(
                id=r["id"],
                email_from=r["email_from"],
                email_subject=r["email_subject"],
                email_snippet=r["email_snippet"],
                classified_intent=r["classified_intent"],
                confidence=r["confidence"],
                reasoning=r["reasoning"],
                action_taken=r["action_taken"],
                boss_feedback=r["boss_feedback"],
                was_correct=r["was_correct"],
            )
            for r in rows
        ]

    async def update_feedback(
        self, record_id: int, feedback: str, was_correct: bool,
    ) -> None:
        """Update boss feedback on an email record."""
        await self._db.execute(
            "UPDATE email_records SET boss_feedback = ?, was_correct = ? WHERE id = ?",
            (feedback, was_correct, record_id),
        )

    async def get_sender_history(self, sender: str, limit: int = 10) -> list[EmailRecord]:
        """Get classification history for a specific sender."""
        rows = await self._db.fetch_all(
            "SELECT * FROM email_records WHERE email_from = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (sender, limit),
        )
        return [
            EmailRecord(
                id=r["id"],
                email_from=r["email_from"],
                email_subject=r["email_subject"],
                email_snippet=r["email_snippet"],
                classified_intent=r["classified_intent"],
                confidence=r["confidence"],
                reasoning=r["reasoning"],
                action_taken=r["action_taken"],
                boss_feedback=r["boss_feedback"],
                was_correct=r["was_correct"],
            )
            for r in rows
        ]

    # -- Learnings --

    async def store_learning(self, learning: AgentLearning) -> int:
        """Store a new learning."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO agent_learnings (learning_type, content, source, email_from) "
            "VALUES (?, ?, ?, ?)",
            (
                learning.learning_type,
                learning.content,
                learning.source,
                learning.email_from,
            ),
        )
        return row_id or 0

    async def get_learnings(
        self, learning_type: str = "", limit: int = 50,
    ) -> list[AgentLearning]:
        """Get agent learnings, optionally filtered by type."""
        if learning_type:
            rows = await self._db.fetch_all(
                "SELECT * FROM agent_learnings WHERE learning_type = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (learning_type, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM agent_learnings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [
            AgentLearning(
                id=r["id"],
                learning_type=r["learning_type"],
                content=r["content"],
                source=r["source"],
                email_from=r["email_from"],
            )
            for r in rows
        ]

    # -- Goals --

    async def upsert_goal(self, goal: AgentGoal) -> int:
        """Insert or update a goal."""
        if goal.id:
            await self._db.execute(
                "UPDATE agent_goals SET description = ?, priority = ?, "
                "progress = ?, status = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (goal.description, goal.priority, goal.progress, goal.status, goal.id),
            )
            return goal.id
        else:
            row_id = await self._db.execute_returning_id(
                "INSERT INTO agent_goals (description, priority, progress, status) "
                "VALUES (?, ?, ?, ?)",
                (goal.description, goal.priority, goal.progress, goal.status),
            )
            return row_id or 0

    async def get_active_goals(self) -> list[AgentGoal]:
        """Get all active goals."""
        rows = await self._db.fetch_all(
            "SELECT * FROM agent_goals WHERE status = 'active' ORDER BY priority DESC",
        )
        return [
            AgentGoal(
                id=r["id"],
                description=r["description"],
                priority=r["priority"],
                progress=r["progress"],
                status=r["status"],
            )
            for r in rows
        ]

    # -- Stats --

    async def get_stats(self) -> dict:
        """Get aggregate statistics for agent state initialization."""
        total = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM email_records",
        ) or 0
        replied = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM email_records WHERE classified_intent = 'reply'",
        ) or 0
        notified = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM email_records WHERE classified_intent = 'notify'",
        ) or 0
        consulted = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM email_records WHERE classified_intent = 'ask_boss'",
        ) or 0

        return {
            "emails_processed": total,
            "emails_replied": replied,
            "notifications_sent": notified,
            "boss_consultations": consulted,
        }

    # -- Notification tracking --

    async def track_notification(
        self, email_record_id: int, tg_message_id: int, tg_chat_id: str,
        notification_text: str = "",
    ) -> int:
        """Track a Telegram notification linked to an email record."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO notification_tracking "
            "(email_record_id, tg_message_id, tg_chat_id, notification_text) "
            "VALUES (?, ?, ?, ?)",
            (email_record_id, tg_message_id, tg_chat_id, notification_text),
        )
        return row_id or 0

    async def get_notification_by_tg_id(
        self, tg_message_id: int,
    ) -> Optional[dict]:
        """Look up a tracked notification by Telegram message ID."""
        row = await self._db.fetch_one(
            "SELECT nt.*, er.email_from, er.email_subject "
            "FROM notification_tracking nt "
            "JOIN email_records er ON nt.email_record_id = er.id "
            "WHERE nt.tg_message_id = ?",
            (tg_message_id,),
        )
        return dict(row) if row else None

    async def record_feedback(
        self, tracking_id: int, text: str, sentiment: str,
    ) -> None:
        """Record principal feedback on a tracked notification."""
        await self._db.execute(
            "UPDATE notification_tracking "
            "SET feedback_received = TRUE, feedback_text = ?, feedback_sentiment = ? "
            "WHERE id = ?",
            (text, sentiment, tracking_id),
        )

    # -- GDPR retention --

    async def purge_gdpr_data(self, retention_days: int = 30) -> int:
        """
        Purge GDPR-sensitive data older than retention period.

        Removes email_snippet and reasoning (which may contain email content)
        from records older than retention_days. The classification metadata
        (intent, confidence, sender address) is retained for aggregate stats.

        Args:
            retention_days: Number of days to retain GDPR data (default 30).

        Returns:
            Number of records scrubbed.
        """
        scrubbed = await self._db.execute(
            "UPDATE email_records "
            "SET email_snippet = '[GDPR purged]', "
            "    reasoning = '[GDPR purged]', "
            "    boss_feedback = CASE WHEN boss_feedback IS NOT NULL "
            "        THEN '[GDPR purged]' ELSE NULL END "
            "WHERE created_at < datetime('now', ? || ' days') "
            "AND email_snippet != '[GDPR purged]'",
            (f"-{retention_days}",),
        )
        if scrubbed > 0:
            logger.info("GDPR: purged sensitive data from %d email records (>%dd old)", scrubbed, retention_days)
        return scrubbed

    async def close(self) -> None:
        """Close the database connection."""
        await self._db.close()
