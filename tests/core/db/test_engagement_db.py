"""
Tests for EngagementDB — unified engagement tracking database.
"""

import pytest
import pytest_asyncio

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB


@pytest_asyncio.fixture
async def db(tmp_path):
    """Provide an initialized in-memory EngagementDB backed by SQLite."""
    config = DatabaseConfig(sqlite_path=str(tmp_path / "engagement.db"))
    backend = SQLiteBackend(config)
    await backend.connect()
    engagement_db = EngagementDB(backend, identity="test")
    await engagement_db.setup()
    yield engagement_db
    await backend.close()


class TestEngagementTracking:
    """Basic engagement recording."""

    @pytest.mark.asyncio
    async def test_record_engagement(self, db):
        """record_engagement() stores an engagement row."""
        await db.record_engagement("post_123", "upvote", 0.8)
        # No error = success (no fetch API exposed, just verify no exception)

    @pytest.mark.asyncio
    async def test_record_heartbeat(self, db):
        """record_heartbeat() stores a heartbeat row."""
        await db.record_heartbeat("post_456", "Some Title")


class TestReplyProcessing:
    """Reply deduplication logic."""

    @pytest.mark.asyncio
    async def test_is_reply_processed_false_initially(self, db):
        """New comment is not processed."""
        result = await db.is_reply_processed("comment_001")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_reply_processed(self, db):
        """mark_reply_processed() persists the result."""
        await db.mark_reply_processed("comment_001", "post_abc", "upvote", 0.7)
        result = await db.is_reply_processed("comment_001")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_processed_via_queue(self, db):
        """Comments in the reply_action_queue count as processed."""
        await db.queue_reply_action("comment_q1", "post_xyz", "reply", 0.6)
        result = await db.is_reply_processed("comment_q1")
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_reply_processed_idempotent(self, db):
        """Double insert is ignored (INSERT OR IGNORE)."""
        await db.mark_reply_processed("comment_dup", "post_abc", "upvote", 0.7)
        await db.mark_reply_processed("comment_dup", "post_abc", "upvote", 0.7)
        assert await db.is_reply_processed("comment_dup") is True


class TestReplyActionQueue:
    """Queue operations."""

    @pytest.mark.asyncio
    async def test_queue_and_get_pending(self, db):
        """Queued items appear in get_pending_reply_actions."""
        await db.queue_reply_action("c1", "p1", "upvote", 0.5)
        await db.queue_reply_action("c2", "p2", "reply", 0.8)

        pending = await db.get_pending_reply_actions()
        assert len(pending) == 2
        ids = {r["comment_id"] for r in pending}
        assert ids == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_remove_from_queue(self, db):
        """remove_from_queue() deletes the item."""
        await db.queue_reply_action("c3", "p3", "upvote", 0.4)
        pending = await db.get_pending_reply_actions()
        assert len(pending) == 1

        queue_id = pending[0]["id"]
        await db.remove_from_queue(queue_id)

        pending_after = await db.get_pending_reply_actions()
        assert len(pending_after) == 0

    @pytest.mark.asyncio
    async def test_update_queue_retry(self, db):
        """update_queue_retry() increments retry_count."""
        await db.queue_reply_action("c4", "p4", "upvote", 0.3)
        pending = await db.get_pending_reply_actions()
        queue_id = pending[0]["id"]
        assert pending[0]["retry_count"] == 0

        await db.update_queue_retry(queue_id, "rate limited")
        pending_after = await db.get_pending_reply_actions()
        assert pending_after[0]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_queue_idempotent(self, db):
        """Queuing the same comment_id twice is ignored."""
        await db.queue_reply_action("c5", "p5", "upvote", 0.5)
        await db.queue_reply_action("c5", "p5", "upvote", 0.5)
        pending = await db.get_pending_reply_actions()
        assert len(pending) == 1


class TestMyPostsTracking:
    """My posts tracking — including the empty post_id guard."""

    @pytest.mark.asyncio
    async def test_track_and_get_my_posts(self, db):
        """track_my_post() persists and get_my_post_ids() returns posts."""
        await db.track_my_post("post_aaa", "Hello World")
        await db.track_my_post("post_bbb", "Second Post")

        ids = await db.get_my_post_ids()
        assert "post_aaa" in ids
        assert "post_bbb" in ids

    @pytest.mark.asyncio
    async def test_track_my_post_empty_id_is_ignored(self, db):
        """track_my_post() must silently skip empty post_id.

        Regression test: Cherry's /posts//comments 404-loop was caused by
        empty strings being stored and then fetched as post IDs.
        """
        await db.track_my_post("", "Some Title")
        ids = await db.get_my_post_ids()
        assert "" not in ids

    @pytest.mark.asyncio
    async def test_get_my_post_ids_filters_empty_strings(self, db):
        """get_my_post_ids() must exclude empty strings even if they exist in DB.

        Belt-and-suspenders: the guard in track_my_post() prevents inserts,
        but existing data in production may have empty strings from before the fix.
        """
        await db.track_my_post("post_real", "Real Post")

        # Manually insert an empty post_id to simulate old production data
        await db._db.execute(
            "INSERT OR IGNORE INTO my_posts (post_id, title) VALUES ('', 'Ghost Post')"
        )

        ids = await db.get_my_post_ids()
        assert "" not in ids
        assert "post_real" in ids

    @pytest.mark.asyncio
    async def test_track_my_post_idempotent(self, db):
        """Tracking the same post_id twice is safe (INSERT OR IGNORE)."""
        await db.track_my_post("post_same", "Same Post")
        await db.track_my_post("post_same", "Same Post")
        ids = await db.get_my_post_ids()
        assert ids.count("post_same") == 1

    @pytest.mark.asyncio
    async def test_untrack_my_post(self, db):
        """untrack_my_post() removes the entry."""
        await db.track_my_post("post_remove", "Remove Me")
        await db.untrack_my_post("post_remove")
        ids = await db.get_my_post_ids()
        assert "post_remove" not in ids

    @pytest.mark.asyncio
    async def test_get_my_post_ids_respects_limit(self, db):
        """get_my_post_ids() respects the limit parameter."""
        for i in range(15):
            await db.track_my_post(f"post_{i:03d}", f"Post {i}")
        ids = await db.get_my_post_ids(limit=5)
        assert len(ids) == 5


class TestMyCommentsTracking:
    """My comments tracking."""

    @pytest.mark.asyncio
    async def test_track_my_comment(self, db):
        """track_my_comment() is idempotent."""
        await db.track_my_comment("cmt_001", "post_x")
        await db.track_my_comment("cmt_001", "post_x")  # duplicate is safe


class TestChallengeTracking:
    """Challenge recording."""

    @pytest.mark.asyncio
    async def test_record_and_get_challenges(self, db):
        """record_challenge() and get_recent_challenges() round-trip."""
        await db.record_challenge(
            challenge_id="ch_001",
            question_raw="What is 2+2?",
            question_clean="2+2",
            answer="4",
            solver="algorithmic",
            correct=True,
            endpoint="/api/v1/verify",
            duration_ms=1.2,
            http_status=200,
        )
        challenges = await db.get_recent_challenges()
        assert len(challenges) == 1
        ch = challenges[0]
        assert ch["challenge_id"] == "ch_001"
        assert ch["correct"] == 1
        assert ch["solver"] == "algorithmic"

    @pytest.mark.asyncio
    async def test_record_challenge_with_error(self, db):
        """record_challenge() handles error fields."""
        await db.record_challenge(
            challenge_id=None,
            question_raw=None,
            question_clean=None,
            answer=None,
            solver="llm",
            correct=False,
            endpoint="/api/v1/verify",
            duration_ms=3000.0,
            http_status=400,
            error="timeout",
        )
        challenges = await db.get_recent_challenges()
        assert challenges[0]["error"] == "timeout"
        assert challenges[0]["correct"] == 0

    @pytest.mark.asyncio
    async def test_get_recent_challenges_respects_limit(self, db):
        """get_recent_challenges() respects the limit parameter."""
        for i in range(5):
            await db.record_challenge(
                challenge_id=f"ch_{i}",
                question_raw="Q",
                question_clean="Q",
                answer="A",
                solver="test",
                correct=True,
                endpoint="/api",
                duration_ms=1.0,
            )
        challenges = await db.get_recent_challenges(limit=3)
        assert len(challenges) == 3
