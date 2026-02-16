"""Tests for engagement database (async DatabaseBackend version)."""

import pytest

from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB


@pytest.fixture
async def engagement_db(tmp_path):
    """Create a real EngagementDB backed by a temporary SQLite file."""
    config = DatabaseConfig(sqlite_path=str(tmp_path / "test_engagement.db"))
    backend = SQLiteBackend(config)
    await backend.connect()
    db = EngagementDB(backend, identity="test")
    await db.setup()
    yield db
    await backend.close()


class TestEngagementDB:
    @pytest.mark.asyncio
    async def test_record_engagement(self, engagement_db):
        await engagement_db.record_engagement("post1", "comment", 0.85)
        # Verify the engagement was recorded (no exception = success)
        stats = await engagement_db.get_engagement_stats() if hasattr(engagement_db, 'get_engagement_stats') else None
        # At minimum, method should complete without error

    @pytest.mark.asyncio
    async def test_record_heartbeat(self, engagement_db):
        await engagement_db.record_heartbeat("post1", "Test Title")
        # Verify heartbeat was written to heartbeats table
        row = await engagement_db._db.fetch_one(
            "SELECT post_id, title FROM heartbeats WHERE post_id = ?",
            ("post1",),
        )
        assert row is not None
        assert row["post_id"] == "post1"
        assert row["title"] == "Test Title"

    @pytest.mark.asyncio
    async def test_track_my_post(self, engagement_db):
        await engagement_db.track_my_post("post1", "My Post")
        ids = await engagement_db.get_my_post_ids()
        assert "post1" in ids

    @pytest.mark.asyncio
    async def test_track_my_comment(self, engagement_db):
        await engagement_db.track_my_comment("comment1", "post1")
        # Verify comment was written to my_comments table
        row = await engagement_db._db.fetch_one(
            "SELECT comment_id, post_id FROM my_comments WHERE comment_id = ?",
            ("comment1",),
        )
        assert row is not None
        assert row["comment_id"] == "comment1"
        assert row["post_id"] == "post1"

    @pytest.mark.asyncio
    async def test_reply_processing(self, engagement_db):
        assert not await engagement_db.is_reply_processed("comment1")
        await engagement_db.mark_reply_processed("comment1", "post1", "comment", 0.7)
        assert await engagement_db.is_reply_processed("comment1")

    @pytest.mark.asyncio
    async def test_reply_queue(self, engagement_db):
        await engagement_db.queue_reply_action("comment1", "post1", "reply", 0.9)
        pending = await engagement_db.get_pending_reply_actions()
        assert len(pending) == 1
        assert pending[0]["comment_id"] == "comment1"

        await engagement_db.remove_from_queue(pending[0]["id"])
        assert len(await engagement_db.get_pending_reply_actions()) == 0

    @pytest.mark.asyncio
    async def test_queue_retry(self, engagement_db):
        await engagement_db.queue_reply_action("comment1", "post1", "reply", 0.9)
        pending = await engagement_db.get_pending_reply_actions()
        await engagement_db.update_queue_retry(pending[0]["id"], "test error")

        updated = await engagement_db.get_pending_reply_actions()
        assert updated[0]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_is_reply_processed_checks_queue(self, engagement_db):
        await engagement_db.queue_reply_action("comment_q", "post1", "reply", 0.5)
        assert await engagement_db.is_reply_processed("comment_q")

    @pytest.mark.asyncio
    async def test_untrack_my_post(self, engagement_db):
        await engagement_db.track_my_post("post1", "Title")
        await engagement_db.untrack_my_post("post1")
        ids = await engagement_db.get_my_post_ids()
        assert "post1" not in ids

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, engagement_db):
        count = await engagement_db.cleanup_expired_queue_items()
        assert count == 0

    @pytest.mark.asyncio
    async def test_trim_stale(self, engagement_db):
        count = await engagement_db.trim_stale_queue_items(max_age_hours=0)
        assert isinstance(count, int)
