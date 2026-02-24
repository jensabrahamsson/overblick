"""
Database layer for the dev agent plugin.

Wraps DatabaseBackend with dev-agent-specific tables for bug tracking,
fix attempt history, and log scan state. Uses the framework's migration
system for schema management.

Agentic tables (goals, actions, learnings, tick logs) are provided
by the core agentic platform via AgenticDB.
"""

import json
import logging
from typing import Optional

from overblick.core.agentic.database import AGENTIC_MIGRATIONS, AgenticDB
from overblick.core.database.base import DatabaseBackend, Migration, MigrationManager
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    FixAttempt,
)

logger = logging.getLogger(__name__)

# Dev-agent-specific migrations (v1-v3)
DEV_AGENT_MIGRATIONS = [
    Migration(
        version=1,
        name="bugs",
        up_sql="""
            CREATE TABLE IF NOT EXISTS bugs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                error_text TEXT DEFAULT '',
                file_path TEXT DEFAULT '',
                identity TEXT DEFAULT '',
                status TEXT DEFAULT 'new',
                priority INTEGER DEFAULT 50,
                fix_attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                branch_name TEXT DEFAULT '',
                pr_url TEXT DEFAULT '',
                analysis TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source, source_ref)
            );
        """,
        down_sql="DROP TABLE IF EXISTS bugs;",
    ),
    Migration(
        version=2,
        name="fix_attempts",
        up_sql="""
            CREATE TABLE IF NOT EXISTS fix_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_id INTEGER NOT NULL,
                attempt_number INTEGER DEFAULT 1,
                analysis TEXT DEFAULT '',
                files_changed TEXT DEFAULT '[]',
                tests_passed INTEGER DEFAULT 0,
                test_output TEXT DEFAULT '',
                opencode_output TEXT DEFAULT '',
                committed INTEGER DEFAULT 0,
                branch_name TEXT DEFAULT '',
                duration_seconds REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (bug_id) REFERENCES bugs(id)
            );
        """,
        down_sql="DROP TABLE IF EXISTS fix_attempts;",
    ),
    Migration(
        version=3,
        name="log_scan_state",
        up_sql="""
            CREATE TABLE IF NOT EXISTS log_scan_state (
                file_path TEXT PRIMARY KEY,
                last_offset INTEGER DEFAULT 0,
                last_scanned TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS log_scan_state;",
    ),
]

# Combined migrations: dev-agent-specific + agentic core
ALL_MIGRATIONS = DEV_AGENT_MIGRATIONS + list(AGENTIC_MIGRATIONS)


class DevAgentDB:
    """
    Database layer for the dev agent plugin.

    Composes AgenticDB for goal/learning/tick queries and adds
    dev-agent-specific methods for bug tracking and fix attempts.
    """

    def __init__(self, db: DatabaseBackend):
        self._db = db
        self._migrations = MigrationManager(db)
        self._agentic = AgenticDB(db)

    @property
    def agentic(self) -> AgenticDB:
        """Access the agentic DB layer for goal/learning/tick queries."""
        return self._agentic

    async def setup(self) -> None:
        """Connect and apply all migrations."""
        await self._db.connect()
        await self._migrations.apply(ALL_MIGRATIONS)

    # ── Bug CRUD ─────────────────────────────────────────────────────────

    async def upsert_bug(self, bug: BugReport) -> int:
        """Insert or update a bug by source + source_ref."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO bugs "
            "(source, source_ref, title, description, error_text, file_path, "
            "identity, status, priority, fix_attempts, max_attempts, "
            "branch_name, pr_url, analysis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, source_ref) DO UPDATE SET "
            "title = ?, description = ?, error_text = ?, status = ?, "
            "priority = ?, fix_attempts = ?, branch_name = ?, pr_url = ?, "
            "analysis = ?, updated_at = datetime('now')",
            (
                bug.source.value, bug.source_ref, bug.title, bug.description,
                bug.error_text, bug.file_path, bug.identity,
                bug.status.value, bug.priority, bug.fix_attempts,
                bug.max_attempts, bug.branch_name, bug.pr_url, bug.analysis,
                # ON CONFLICT SET:
                bug.title, bug.description, bug.error_text, bug.status.value,
                bug.priority, bug.fix_attempts, bug.branch_name, bug.pr_url,
                bug.analysis,
            ),
        )
        return row_id or 0

    async def get_bug(self, bug_id: int) -> Optional[BugReport]:
        """Get a bug by ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM bugs WHERE id = ?", (bug_id,),
        )
        return self._row_to_bug(row) if row else None

    async def get_bug_by_ref(self, source: str, source_ref: str) -> Optional[BugReport]:
        """Get a bug by source + source_ref."""
        row = await self._db.fetch_one(
            "SELECT * FROM bugs WHERE source = ? AND source_ref = ?",
            (source, source_ref),
        )
        return self._row_to_bug(row) if row else None

    async def get_active_bugs(self) -> list[BugReport]:
        """Get all bugs that are not in a terminal state."""
        rows = await self._db.fetch_all(
            "SELECT * FROM bugs WHERE status NOT IN ('fixed', 'skipped') "
            "ORDER BY priority DESC, created_at ASC",
        )
        return [self._row_to_bug(r) for r in rows]

    async def get_bugs_by_status(self, status: str) -> list[BugReport]:
        """Get all bugs with a given status."""
        rows = await self._db.fetch_all(
            "SELECT * FROM bugs WHERE status = ? ORDER BY priority DESC",
            (status,),
        )
        return [self._row_to_bug(r) for r in rows]

    async def update_bug_status(
        self, bug_id: int, status: str, **kwargs: str,
    ) -> None:
        """Update a bug's status and optional fields."""
        updates = ["status = ?", "updated_at = datetime('now')"]
        params: list = [status]

        for key in ("branch_name", "pr_url", "analysis"):
            if key in kwargs:
                updates.append(f"{key} = ?")
                params.append(kwargs[key])

        if "fix_attempts" in kwargs:
            updates.append("fix_attempts = ?")
            params.append(kwargs["fix_attempts"])

        params.append(bug_id)
        await self._db.execute(
            f"UPDATE bugs SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

    # ── Fix attempt CRUD ─────────────────────────────────────────────────

    async def record_fix_attempt(self, attempt: FixAttempt) -> int:
        """Record a fix attempt."""
        files_json = json.dumps(attempt.files_changed)
        row_id = await self._db.execute_returning_id(
            "INSERT INTO fix_attempts "
            "(bug_id, attempt_number, analysis, files_changed, tests_passed, "
            "test_output, opencode_output, committed, branch_name, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt.bug_id, attempt.attempt_number, attempt.analysis,
                files_json, 1 if attempt.tests_passed else 0,
                attempt.test_output, attempt.opencode_output,
                1 if attempt.committed else 0, attempt.branch_name,
                attempt.duration_seconds,
            ),
        )
        return row_id or 0

    async def get_fix_attempts(self, bug_id: int) -> list[FixAttempt]:
        """Get all fix attempts for a bug."""
        rows = await self._db.fetch_all(
            "SELECT * FROM fix_attempts WHERE bug_id = ? ORDER BY attempt_number",
            (bug_id,),
        )
        return [self._row_to_attempt(r) for r in rows]

    async def get_recent_attempts(self, limit: int = 10) -> list[FixAttempt]:
        """Get most recent fix attempts across all bugs."""
        rows = await self._db.fetch_all(
            "SELECT * FROM fix_attempts ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_attempt(r) for r in rows]

    # ── Log scan state ───────────────────────────────────────────────────

    async def get_log_offset(self, file_path: str) -> int:
        """Get the last scanned byte offset for a log file."""
        row = await self._db.fetch_one(
            "SELECT last_offset FROM log_scan_state WHERE file_path = ?",
            (file_path,),
        )
        return row["last_offset"] if row else 0

    async def update_log_offset(self, file_path: str, offset: int) -> None:
        """Update the byte offset for a log file."""
        await self._db.execute(
            "INSERT INTO log_scan_state (file_path, last_offset) "
            "VALUES (?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "last_offset = ?, last_scanned = datetime('now')",
            (file_path, offset, offset),
        )

    # ── Stats ────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        """Get aggregate statistics."""
        total_bugs = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM bugs",
        ) or 0
        fixed = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM bugs WHERE status = 'fixed'",
        ) or 0
        failed = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM bugs WHERE status = 'failed'",
        ) or 0
        attempts = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM fix_attempts",
        ) or 0
        prs = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM bugs WHERE pr_url != ''",
        ) or 0

        return {
            "total_bugs": total_bugs,
            "bugs_fixed": fixed,
            "bugs_failed": failed,
            "fix_attempts": attempts,
            "prs_created": prs,
        }

    async def close(self) -> None:
        """Close the database connection."""
        await self._db.close()

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_bug(row: dict) -> BugReport:
        """Convert a database row to a BugReport."""
        return BugReport(
            id=row["id"],
            source=BugSource(row["source"]),
            source_ref=row["source_ref"],
            title=row["title"],
            description=row.get("description", ""),
            error_text=row.get("error_text", ""),
            file_path=row.get("file_path", ""),
            identity=row.get("identity", ""),
            status=BugStatus(row["status"]),
            priority=row.get("priority", 50),
            fix_attempts=row.get("fix_attempts", 0),
            max_attempts=row.get("max_attempts", 3),
            branch_name=row.get("branch_name", ""),
            pr_url=row.get("pr_url", ""),
            analysis=row.get("analysis", ""),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

    @staticmethod
    def _row_to_attempt(row: dict) -> FixAttempt:
        """Convert a database row to a FixAttempt."""
        files = []
        raw = row.get("files_changed", "[]")
        if raw:
            try:
                files = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass

        return FixAttempt(
            id=row["id"],
            bug_id=row["bug_id"],
            attempt_number=row.get("attempt_number", 1),
            analysis=row.get("analysis", ""),
            files_changed=files,
            tests_passed=bool(row.get("tests_passed", 0)),
            test_output=row.get("test_output", ""),
            opencode_output=row.get("opencode_output", ""),
            committed=bool(row.get("committed", 0)),
            branch_name=row.get("branch_name", ""),
            duration_seconds=row.get("duration_seconds", 0.0),
            created_at=row.get("created_at", ""),
        )
