"""
Agentic database layer — shared migrations and query methods.

Provides AGENTIC_MIGRATIONS (v900+) that any agentic plugin can append
to its own migration list, plus AgenticDB with query methods for goals,
learnings, tick logs, and action logs.

Uses CREATE TABLE IF NOT EXISTS for idempotency — safe to apply even
if the plugin already has these tables from earlier schema versions.
"""

import json
import logging
from typing import Any, Optional

from overblick.core.agentic.models import (
    ActionOutcome,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    TickLog,
)
from overblick.core.database.base import DatabaseBackend, Migration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agentic migrations (v900+)
#
# v900-v903: Core agentic tables (IF NOT EXISTS for idempotency)
# v904: Add missing columns to existing tables (for GitHub upgrade path)
# ---------------------------------------------------------------------------

AGENTIC_MIGRATIONS = [
    Migration(
        version=900,
        name="agentic_goals",
        up_sql="""
            CREATE TABLE IF NOT EXISTS agent_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                priority INTEGER DEFAULT 50,
                status TEXT DEFAULT 'active',
                progress REAL DEFAULT 0.0,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS agent_goals;",
    ),
    Migration(
        version=901,
        name="agentic_action_log",
        up_sql="""
            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_number INTEGER DEFAULT 0,
                action_type TEXT NOT NULL,
                target TEXT DEFAULT '',
                target_number INTEGER DEFAULT 0,
                repo TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                reasoning TEXT DEFAULT '',
                success INTEGER DEFAULT 0,
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                duration_ms REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS action_log;",
    ),
    Migration(
        version=902,
        name="agentic_learnings",
        up_sql="""
            CREATE TABLE IF NOT EXISTS agent_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT '',
                insight TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'reflection',
                source_tick INTEGER DEFAULT 0,
                source_ref TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS agent_learnings;",
    ),
    Migration(
        version=903,
        name="agentic_tick_log",
        up_sql="""
            CREATE TABLE IF NOT EXISTS tick_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_number INTEGER NOT NULL,
                started_at TEXT DEFAULT '',
                completed_at TEXT DEFAULT '',
                observations_count INTEGER DEFAULT 0,
                actions_planned INTEGER DEFAULT 0,
                actions_executed INTEGER DEFAULT 0,
                actions_succeeded INTEGER DEFAULT 0,
                reasoning_summary TEXT DEFAULT '',
                duration_ms REAL DEFAULT 0.0
            );
        """,
        down_sql="DROP TABLE IF EXISTS tick_log;",
    ),
]


class AgenticDB:
    """
    Shared agentic database query methods.

    Wraps a DatabaseBackend with goal, learning, tick log, and action
    log operations. Plugins compose this into their own DB class or
    use it directly via AgenticPluginBase.
    """

    def __init__(self, db: DatabaseBackend):
        self._db = db

    # ── Agent goals ────────────────────────────────────────────────────────

    async def get_goals(self, status: str = "active") -> list[AgentGoal]:
        """Get all goals with the given status."""
        rows = await self._db.fetch_all(
            "SELECT * FROM agent_goals WHERE status = ? ORDER BY priority DESC",
            (status,),
        )
        return [self._row_to_goal(r) for r in rows]

    async def upsert_goal(self, goal: AgentGoal) -> int:
        """Insert or update a goal by name."""
        metadata_json = json.dumps(goal.metadata) if goal.metadata else "{}"
        row_id = await self._db.execute_returning_id(
            "INSERT INTO agent_goals (name, description, priority, status, progress, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "description = ?, priority = ?, status = ?, progress = ?, "
            "metadata = ?, updated_at = datetime('now')",
            (
                goal.name, goal.description, goal.priority,
                goal.status.value, goal.progress, metadata_json,
                goal.description, goal.priority, goal.status.value,
                goal.progress, metadata_json,
            ),
        )
        return row_id or 0

    async def get_goal_by_name(self, name: str) -> Optional[AgentGoal]:
        """Get a single goal by name."""
        row = await self._db.fetch_one(
            "SELECT * FROM agent_goals WHERE name = ?", (name,),
        )
        return self._row_to_goal(row) if row else None

    # ── Action log ────────────────────────────────────────────────────────

    async def log_action(self, tick_number: int, outcome: ActionOutcome) -> int:
        """Record an executed action."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO action_log "
            "(tick_number, action_type, target, target_number, repo, "
            "priority, reasoning, success, result, error, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tick_number,
                outcome.action.action_type,
                outcome.action.target,
                outcome.action.target_number,
                outcome.action.repo,
                outcome.action.priority,
                outcome.action.reasoning,
                1 if outcome.success else 0,
                outcome.result,
                outcome.error,
                outcome.duration_ms,
            ),
        )
        return row_id or 0

    async def get_recent_actions(self, limit: int = 20) -> list[dict]:
        """Get recent action log entries."""
        return await self._db.fetch_all(
            "SELECT * FROM action_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    # ── Learnings ─────────────────────────────────────────────────────────

    async def add_learning(self, learning: AgentLearning) -> int:
        """Record a new learning."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO agent_learnings "
            "(category, insight, confidence, source, source_tick, source_ref) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                learning.category, learning.insight, learning.confidence,
                learning.source, learning.source_tick, learning.source_ref,
            ),
        )
        return row_id or 0

    async def get_learnings(self, limit: int = 20) -> list[AgentLearning]:
        """Get recent learnings."""
        rows = await self._db.fetch_all(
            "SELECT * FROM agent_learnings ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_learning(r) for r in rows]

    # ── Tick log ──────────────────────────────────────────────────────────

    async def log_tick(self, tick: TickLog) -> int:
        """Record a tick cycle."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO tick_log "
            "(tick_number, started_at, completed_at, observations_count, "
            "actions_planned, actions_executed, actions_succeeded, "
            "reasoning_summary, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tick.tick_number, tick.started_at, tick.completed_at,
                tick.observations_count, tick.actions_planned,
                tick.actions_executed, tick.actions_succeeded,
                tick.reasoning_summary, tick.duration_ms,
            ),
        )
        return row_id or 0

    async def get_tick_count(self) -> int:
        """Get the total number of recorded ticks."""
        count = await self._db.fetch_scalar("SELECT COUNT(*) FROM tick_log")
        return count or 0

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_goal(row: dict[str, Any]) -> AgentGoal:
        """Convert a database row to an AgentGoal."""
        metadata = {}
        raw_meta = row.get("metadata", "{}")
        if raw_meta:
            try:
                metadata = json.loads(raw_meta)
            except (json.JSONDecodeError, TypeError):
                pass

        return AgentGoal(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            priority=row["priority"],
            status=GoalStatus(row["status"]),
            progress=row["progress"],
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            metadata=metadata,
        )

    @staticmethod
    def _row_to_learning(row: dict[str, Any]) -> AgentLearning:
        """Convert a database row to an AgentLearning."""
        return AgentLearning(
            id=row["id"],
            category=row.get("category", ""),
            insight=row.get("insight", ""),
            confidence=row.get("confidence", 0.5),
            source=row.get("source", "reflection"),
            source_tick=row.get("source_tick", 0),
            source_ref=row.get("source_ref"),
            created_at=row.get("created_at", ""),
        )
