"""
Database migrations for Blick.

Each migration is a versioned SQL script that evolves the schema.
Migrations are idempotent — they use IF NOT EXISTS where possible.
"""

from blick.core.database.base import Migration

# All migrations in order
MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        name="initial_schema",
        up_sql="""
            -- Engagement tracking
            CREATE TABLE IF NOT EXISTS engagements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL,
                relevance_score REAL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Heartbeat tracking
            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Reply tracking
            CREATE TABLE IF NOT EXISTS processed_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT NOT NULL,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL,
                relevance_score REAL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_processed_replies_cid
                ON processed_replies(comment_id);

            -- My content tracking
            CREATE TABLE IF NOT EXISTS my_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL UNIQUE,
                title TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS my_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT NOT NULL UNIQUE,
                post_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Reply action queue
            CREATE TABLE IF NOT EXISTS reply_action_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT NOT NULL,
                post_id TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'reply',
                relevance_score REAL,
                retry_count INTEGER DEFAULT 0,
                last_attempt TEXT,
                error_message TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_queue_expires
                ON reply_action_queue(expires_at);

            -- Audit log (append-only)
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                identity TEXT NOT NULL,
                plugin TEXT,
                details TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                duration_ms REAL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
            CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category);
        """,
    ),

    Migration(
        version=2,
        name="add_agent_state_tables",
        up_sql="""
            -- Dreams persistence
            CREATE TABLE IF NOT EXISTS dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dream_type TEXT NOT NULL,
                content TEXT,
                symbols TEXT,
                tone TEXT,
                insight TEXT,
                topics_referenced TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Therapy sessions
            CREATE TABLE IF NOT EXISTS therapy_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_number INTEGER,
                dreams_processed INTEGER DEFAULT 0,
                learnings_processed INTEGER DEFAULT 0,
                dream_themes TEXT,
                learning_themes TEXT,
                synthesis_insights TEXT,
                session_summary TEXT,
                post_title TEXT,
                post_content TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Learnings (proposed, reviewed, approved/rejected)
            CREATE TABLE IF NOT EXISTS learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learning_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                content TEXT,
                source_context TEXT,
                source_agent TEXT,
                proposed_at TEXT,
                review_result TEXT,
                review_reason TEXT,
                reviewed_at TEXT,
                stored INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Emotional state snapshots
            CREATE TABLE IF NOT EXISTS emotional_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mood TEXT NOT NULL,
                mood_intensity REAL,
                positive_interactions INTEGER DEFAULT 0,
                negative_interactions INTEGER DEFAULT 0,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Feed deduplication (persistent seen posts)
            CREATE TABLE IF NOT EXISTS seen_posts (
                post_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """,
    ),

    Migration(
        version=3,
        name="add_agent_audit_tables",
        up_sql="""
            -- Agent audit reports (boss agent feature)
            CREATE TABLE IF NOT EXISTS agent_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                metrics TEXT,
                findings TEXT,
                recommendations TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Prompt tweaks history (from audit insights)
            CREATE TABLE IF NOT EXISTS prompt_tweaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id INTEGER REFERENCES agent_audits(id),
                tweak_type TEXT NOT NULL,
                original_value TEXT,
                new_value TEXT,
                reason TEXT,
                applied INTEGER DEFAULT 0,
                applied_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """,
    ),
]
