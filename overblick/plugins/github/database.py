"""
Database layer for the GitHub agent plugin.

Wraps DatabaseBackend with GitHub-specific tables for event
deduplication, comment tracking, file tree/content caching,
and PR tracking. Uses the framework's migration system for
schema management.

Agentic tables (goals, actions, learnings, tick logs) are provided
by the core agentic platform via AgenticDB.
"""

import logging
from typing import Any, Optional

from overblick.core.agentic.database import AGENTIC_MIGRATIONS, AgenticDB
from overblick.core.database.base import DatabaseBackend, Migration, MigrationManager
from overblick.plugins.github.models import (
    CachedFile,
    CommentRecord,
    EventRecord,
    FileTreeEntry,
)

logger = logging.getLogger(__name__)

# GitHub-specific migrations (v1-v5 + v10-v11)
GITHUB_MIGRATIONS = [
    Migration(
        version=1,
        name="events_seen",
        up_sql="""
            CREATE TABLE IF NOT EXISTS events_seen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                repo TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                author TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                action_taken TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS events_seen;",
    ),
    Migration(
        version=2,
        name="comments_posted",
        up_sql="""
            CREATE TABLE IF NOT EXISTS comments_posted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                github_comment_id INTEGER NOT NULL,
                repo TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                content_hash TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS comments_posted;",
    ),
    Migration(
        version=3,
        name="file_tree_cache",
        up_sql="""
            CREATE TABLE IF NOT EXISTS file_tree_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                path TEXT NOT NULL,
                sha TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                last_refreshed TEXT DEFAULT (datetime('now')),
                UNIQUE(repo, path)
            );
        """,
        down_sql="DROP TABLE IF EXISTS file_tree_cache;",
    ),
    Migration(
        version=4,
        name="file_content_cache",
        up_sql="""
            CREATE TABLE IF NOT EXISTS file_content_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                path TEXT NOT NULL,
                sha TEXT NOT NULL,
                content TEXT DEFAULT '',
                cached_at TEXT DEFAULT (datetime('now')),
                UNIQUE(repo, sha)
            );
        """,
        down_sql="DROP TABLE IF EXISTS file_content_cache;",
    ),
    Migration(
        version=5,
        name="repo_tree_meta",
        up_sql="""
            CREATE TABLE IF NOT EXISTS repo_tree_meta (
                repo TEXT PRIMARY KEY,
                root_sha TEXT DEFAULT '',
                last_refreshed TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS repo_tree_meta;",
    ),
    # Skip v6-v9 — these were the old agentic migrations, now handled by
    # AGENTIC_MIGRATIONS (v900+). For existing databases that already have
    # v6-v9, the v900+ migrations are no-ops due to IF NOT EXISTS.
    # For new databases, v6-v9 are skipped and v900+ create the tables.
    Migration(
        version=10,
        name="pr_tracking",
        up_sql="""
            CREATE TABLE IF NOT EXISTS pr_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                title TEXT DEFAULT '',
                author TEXT DEFAULT '',
                is_dependabot INTEGER DEFAULT 0,
                version_bump TEXT DEFAULT '',
                ci_status TEXT DEFAULT 'unknown',
                merged INTEGER DEFAULT 0,
                auto_merged INTEGER DEFAULT 0,
                first_seen TEXT DEFAULT (datetime('now')),
                last_checked TEXT DEFAULT (datetime('now')),
                UNIQUE(repo, pr_number)
            );
        """,
        down_sql="DROP TABLE IF EXISTS pr_tracking;",
    ),
    Migration(
        version=11,
        name="repo_summaries",
        up_sql="""
            CREATE TABLE IF NOT EXISTS repo_summaries (
                repo TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                file_count INTEGER DEFAULT 0,
                primary_language TEXT DEFAULT '',
                last_generated TEXT DEFAULT (datetime('now'))
            );
        """,
        down_sql="DROP TABLE IF EXISTS repo_summaries;",
    ),
]

# Combined migrations: GitHub-specific + agentic core
ALL_MIGRATIONS = GITHUB_MIGRATIONS + list(AGENTIC_MIGRATIONS)


class GitHubDB:
    """
    Database layer for the GitHub plugin.

    Composes AgenticDB for goal/learning/tick queries and adds
    GitHub-specific methods for events, comments, files, and PR tracking.
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

    # ── Delegate agentic methods ─────────────────────────────────────────

    async def get_goals(self, status: str = "active"):
        return await self._agentic.get_goals(status)

    async def upsert_goal(self, goal):
        return await self._agentic.upsert_goal(goal)

    async def get_goal_by_name(self, name: str):
        return await self._agentic.get_goal_by_name(name)

    async def log_action(self, tick_number, outcome):
        return await self._agentic.log_action(tick_number, outcome)

    async def get_recent_actions(self, limit=20):
        return await self._agentic.get_recent_actions(limit)

    async def add_learning(self, learning):
        return await self._agentic.add_learning(learning)

    async def get_learnings(self, limit=20):
        return await self._agentic.get_learnings(limit)

    async def log_tick(self, tick):
        return await self._agentic.log_tick(tick)

    async def get_tick_count(self):
        return await self._agentic.get_tick_count()

    # ── Event deduplication ───────────────────────────────────────────────

    async def has_event(self, event_id: str) -> bool:
        """Check if an event has already been processed."""
        count = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM events_seen WHERE event_id = ?",
            (event_id,),
        )
        return (count or 0) > 0

    async def record_event(self, record: EventRecord) -> int:
        """Record a processed event. Returns row ID."""
        row_id = await self._db.execute_returning_id(
            "INSERT OR IGNORE INTO events_seen "
            "(event_id, event_type, repo, issue_number, author, score, action_taken) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.event_id,
                record.event_type,
                record.repo,
                record.issue_number,
                record.author,
                record.score,
                record.action_taken,
            ),
        )
        return row_id or 0

    async def has_responded_to_issue(self, repo: str, issue_number: int) -> bool:
        """Check if we've already responded to a specific issue."""
        count = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM comments_posted WHERE repo = ? AND issue_number = ?",
            (repo, issue_number),
        )
        return (count or 0) > 0

    async def get_response_count(self, repo: str, issue_number: int) -> int:
        """Get the number of responses we've posted to an issue."""
        count = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM comments_posted WHERE repo = ? AND issue_number = ?",
            (repo, issue_number),
        )
        return count or 0

    # ── Comment tracking ──────────────────────────────────────────────────

    async def record_comment(self, record: CommentRecord) -> int:
        """Record a comment we posted."""
        row_id = await self._db.execute_returning_id(
            "INSERT INTO comments_posted "
            "(github_comment_id, repo, issue_number, content_hash) "
            "VALUES (?, ?, ?, ?)",
            (
                record.github_comment_id,
                record.repo,
                record.issue_number,
                record.content_hash,
            ),
        )
        return row_id or 0

    # ── File tree cache ───────────────────────────────────────────────────

    async def get_tree_meta(self, repo: str) -> Optional[dict]:
        """Get cached tree metadata (root sha + last refresh time)."""
        row = await self._db.fetch_one(
            "SELECT * FROM repo_tree_meta WHERE repo = ?", (repo,),
        )
        return dict(row) if row else None

    async def update_tree_meta(self, repo: str, root_sha: str) -> None:
        """Update or insert tree metadata."""
        existing = await self.get_tree_meta(repo)
        if existing:
            await self._db.execute(
                "UPDATE repo_tree_meta SET root_sha = ?, last_refreshed = datetime('now') "
                "WHERE repo = ?",
                (root_sha, repo),
            )
        else:
            await self._db.execute(
                "INSERT INTO repo_tree_meta (repo, root_sha) VALUES (?, ?)",
                (repo, root_sha),
            )

    async def upsert_tree_entry(self, repo: str, entry: FileTreeEntry) -> None:
        """Insert or update a file tree entry."""
        await self._db.execute(
            "INSERT INTO file_tree_cache (repo, path, sha, size, last_refreshed) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(repo, path) DO UPDATE SET sha = ?, size = ?, last_refreshed = datetime('now')",
            (repo, entry.path, entry.sha, entry.size, entry.sha, entry.size),
        )

    async def get_tree_paths(self, repo: str) -> list[str]:
        """Get all cached file paths for a repo."""
        rows = await self._db.fetch_all(
            "SELECT path FROM file_tree_cache WHERE repo = ? ORDER BY path",
            (repo,),
        )
        return [r["path"] for r in rows]

    async def clear_tree(self, repo: str) -> None:
        """Clear all cached tree entries for a repo."""
        await self._db.execute(
            "DELETE FROM file_tree_cache WHERE repo = ?", (repo,),
        )

    # ── File content cache ────────────────────────────────────────────────

    async def get_cached_file(self, repo: str, sha: str) -> Optional[CachedFile]:
        """Get a cached file by repo + sha (content-addressable)."""
        row = await self._db.fetch_one(
            "SELECT * FROM file_content_cache WHERE repo = ? AND sha = ?",
            (repo, sha),
        )
        if not row:
            return None
        return CachedFile(
            repo=row["repo"],
            path=row["path"],
            sha=row["sha"],
            content=row["content"],
            cached_at=row["cached_at"],
        )

    async def get_cached_file_by_path(self, repo: str, path: str) -> Optional[CachedFile]:
        """Get the most recent cached file by repo + path."""
        row = await self._db.fetch_one(
            "SELECT * FROM file_content_cache WHERE repo = ? AND path = ? "
            "ORDER BY cached_at DESC LIMIT 1",
            (repo, path),
        )
        if not row:
            return None
        return CachedFile(
            repo=row["repo"],
            path=row["path"],
            sha=row["sha"],
            content=row["content"],
            cached_at=row["cached_at"],
        )

    async def cache_file(self, repo: str, path: str, sha: str, content: str) -> None:
        """Cache file content (content-addressable by sha)."""
        await self._db.execute(
            "INSERT INTO file_content_cache (repo, path, sha, content) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(repo, sha) DO UPDATE SET path = ?, content = ?, cached_at = datetime('now')",
            (repo, path, sha, content, path, content),
        )

    async def get_file_sha(self, repo: str, path: str) -> Optional[str]:
        """Get the cached sha for a file path (from tree cache)."""
        row = await self._db.fetch_one(
            "SELECT sha FROM file_tree_cache WHERE repo = ? AND path = ?",
            (repo, path),
        )
        return row["sha"] if row else None

    # ── PR tracking ───────────────────────────────────────────────────────

    async def upsert_pr_tracking(
        self, repo: str, pr_number: int, **kwargs: Any,
    ) -> None:
        """Insert or update PR tracking record."""
        title = kwargs.get("title", "")
        author = kwargs.get("author", "")
        is_dependabot = 1 if kwargs.get("is_dependabot", False) else 0
        version_bump = kwargs.get("version_bump", "")
        ci_status = kwargs.get("ci_status", "unknown")
        merged = 1 if kwargs.get("merged", False) else 0
        auto_merged = 1 if kwargs.get("auto_merged", False) else 0

        await self._db.execute(
            "INSERT INTO pr_tracking "
            "(repo, pr_number, title, author, is_dependabot, version_bump, "
            "ci_status, merged, auto_merged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(repo, pr_number) DO UPDATE SET "
            "ci_status = ?, merged = ?, auto_merged = ?, last_checked = datetime('now')",
            (
                repo, pr_number, title, author, is_dependabot,
                version_bump, ci_status, merged, auto_merged,
                ci_status, merged, auto_merged,
            ),
        )

    async def was_pr_auto_merged(self, repo: str, pr_number: int) -> bool:
        """Check if a PR was already auto-merged by us."""
        count = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM pr_tracking "
            "WHERE repo = ? AND pr_number = ? AND auto_merged = 1",
            (repo, pr_number),
        )
        return (count or 0) > 0

    # ── Repo summaries ────────────────────────────────────────────────────

    async def get_repo_summary(self, repo: str) -> Optional[dict]:
        """Get cached repo summary."""
        row = await self._db.fetch_one(
            "SELECT * FROM repo_summaries WHERE repo = ?", (repo,),
        )
        return dict(row) if row else None

    async def upsert_repo_summary(
        self, repo: str, summary: str, file_count: int = 0,
        primary_language: str = "",
    ) -> None:
        """Insert or update repo summary."""
        await self._db.execute(
            "INSERT INTO repo_summaries (repo, summary, file_count, primary_language) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(repo) DO UPDATE SET "
            "summary = ?, file_count = ?, primary_language = ?, "
            "last_generated = datetime('now')",
            (repo, summary, file_count, primary_language,
             summary, file_count, primary_language),
        )

    # ── Stats ─────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        """Get aggregate statistics."""
        events = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM events_seen",
        ) or 0
        comments = await self._db.fetch_scalar(
            "SELECT COUNT(*) FROM comments_posted",
        ) or 0
        repos = await self._db.fetch_scalar(
            "SELECT COUNT(DISTINCT repo) FROM events_seen",
        ) or 0

        return {
            "events_processed": events,
            "comments_posted": comments,
            "repos_tracked": repos,
        }

    async def close(self) -> None:
        """Close the database connection."""
        await self._db.close()
