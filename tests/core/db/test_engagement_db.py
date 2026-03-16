"""
Tests for EngagementDB — unified engagement tracking database.
"""

import asyncio
from unittest.mock import AsyncMock, patch

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

    @pytest.mark.asyncio
    async def test_get_my_comment_post_ids(self, db):
        """get_my_comment_post_ids() returns distinct post_ids."""
        await db.track_my_comment("cmt_001", "post_a")
        await db.track_my_comment("cmt_002", "post_b")
        await db.track_my_comment("cmt_003", "post_a")  # duplicate post_id
        ids = await db.get_my_comment_post_ids(limit=10)
        assert set(ids) == {"post_a", "post_b"}

    @pytest.mark.asyncio
    async def test_get_my_comment_post_ids_respects_limit(self, db):
        """get_my_comment_post_ids() respects limit."""
        for i in range(10):
            await db.track_my_comment(f"cmt_{i:03d}", f"post_{i:03d}")
        ids = await db.get_my_comment_post_ids(limit=3)
        assert len(ids) == 3

    @pytest.mark.asyncio
    async def test_get_my_comment_ids_for_post(self, db):
        """get_my_comment_ids_for_post() returns our comment_ids on a specific post."""
        await db.track_my_comment("cmt_001", "post_x")
        await db.track_my_comment("cmt_002", "post_x")
        await db.track_my_comment("cmt_003", "post_y")  # different post
        ids = await db.get_my_comment_ids_for_post("post_x")
        assert set(ids) == {"cmt_001", "cmt_002"}

    @pytest.mark.asyncio
    async def test_get_my_comment_ids_for_post_empty(self, db):
        """get_my_comment_ids_for_post() returns empty list for unknown post."""
        ids = await db.get_my_comment_ids_for_post("nonexistent")
        assert ids == []


class TestRecentInteractions:
    """Recent interactions for learning-based heartbeat prompts."""

    @pytest.mark.asyncio
    async def test_get_recent_interactions(self, db):
        """get_recent_interactions() returns engagement records."""
        await db.record_engagement("post_1", "comment", 0.8)
        await db.record_engagement("post_2", "upvote", 0.5)
        interactions = await db.get_recent_interactions(limit=5)
        assert len(interactions) == 2
        assert interactions[0]["action"] in ("comment", "upvote")

    @pytest.mark.asyncio
    async def test_get_recent_interactions_respects_limit(self, db):
        """get_recent_interactions() respects limit."""
        for i in range(10):
            await db.record_engagement(f"post_{i}", "comment", 0.5)
        interactions = await db.get_recent_interactions(limit=3)
        assert len(interactions) == 3

    @pytest.mark.asyncio
    async def test_get_recent_interactions_empty(self, db):
        """get_recent_interactions() returns empty list when no engagements."""
        interactions = await db.get_recent_interactions(limit=5)
        assert interactions == []


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


class TestBackgroundCleanup:
    """Background cleanup lifecycle (lines 136-165)."""

    @pytest.mark.asyncio
    async def test_should_start_background_cleanup_task(self, db):
        db.start_background_cleanup()
        assert db._cleanup_task is not None
        assert not db._cleanup_task.done()
        db.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_should_not_start_duplicate_cleanup_task(self, db):
        db.start_background_cleanup()
        first_task = db._cleanup_task
        db.start_background_cleanup()  # should be no-op
        assert db._cleanup_task is first_task
        db.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_should_stop_background_cleanup(self, db):
        db.start_background_cleanup()
        db.stop_background_cleanup()
        assert db._cleanup_task is None

    @pytest.mark.asyncio
    async def test_should_handle_stop_when_no_task(self, db):
        db.stop_background_cleanup()  # no-op, should not raise

    @pytest.mark.asyncio
    async def test_should_run_cleanup_loop_and_handle_cancel(self, db):
        """_cleanup_loop exits cleanly on CancelledError."""
        with patch.object(db, '_CLEANUP_INTERVAL_SECONDS', 0.01):
            db.start_background_cleanup()
            await asyncio.sleep(0.05)  # let it run a cycle
            db.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_should_handle_error_in_cleanup_loop(self, db):
        """_cleanup_loop logs warning on trim error and continues."""
        with patch.object(db, 'trim_old_entries', side_effect=RuntimeError("db error")), \
             patch.object(db, '_CLEANUP_INTERVAL_SECONDS', 0.01), \
             patch("overblick.core.db.engagement_db.logger") as mock_logger:
            db.start_background_cleanup()
            await asyncio.sleep(0.05)
            db.stop_background_cleanup()
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_should_log_when_cleanup_trims_entries(self, db):
        """_cleanup_loop logs debug when entries are trimmed."""
        with patch.object(db, 'trim_old_entries', new_callable=AsyncMock, return_value=5), \
             patch.object(db, '_CLEANUP_INTERVAL_SECONDS', 0.01), \
             patch("overblick.core.db.engagement_db.logger") as mock_logger:
            db.start_background_cleanup()
            await asyncio.sleep(0.05)
            db.stop_background_cleanup()
            mock_logger.debug.assert_called()


class TestTrimOldEntries:
    """Retention trimming (lines 174-202)."""

    @pytest.mark.asyncio
    async def test_should_return_zero_when_no_old_entries(self, db):
        deleted = await db.trim_old_entries()
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_should_trim_old_entries_and_log(self, db):
        # Insert data with old timestamps
        await db._db.execute(
            "INSERT INTO engagements (post_id, action, relevance_score, created_at) "
            "VALUES (?, ?, ?, datetime('now', '-100 days'))",
            ("old_post", "upvote", 0.5),
        )
        await db._db.execute(
            "INSERT INTO heartbeats (post_id, title, created_at) "
            "VALUES (?, ?, datetime('now', '-100 days'))",
            ("old_hb", "Old Title"),
        )
        with patch("overblick.core.db.engagement_db.logger") as mock_logger:
            deleted = await db.trim_old_entries(retention_days=90)
        assert deleted >= 2
        mock_logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_should_handle_non_int_execute_result(self, db):
        """If execute returns non-int, treat as 0."""
        with patch.object(db._db, 'execute', new_callable=AsyncMock, return_value="not_an_int"):
            deleted = await db.trim_old_entries()
        assert deleted == 0


class TestFeedDeduplication:
    """Feed deduplication: is_post_seen, mark_post_seen, are_posts_seen."""

    @pytest.mark.asyncio
    async def test_should_return_false_for_unseen_post(self, db):
        result = await db.is_post_seen("new_post")
        assert result is False

    @pytest.mark.asyncio
    async def test_should_return_true_after_marking_seen(self, db):
        await db.mark_post_seen("post_x")
        result = await db.is_post_seen("post_x")
        assert result is True

    @pytest.mark.asyncio
    async def test_should_return_empty_dict_for_empty_list(self, db):
        result = await db.are_posts_seen([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_should_batch_check_seen_posts(self, db):
        await db.mark_post_seen("seen1")
        await db.mark_post_seen("seen2")
        result = await db.are_posts_seen(["seen1", "seen2", "unseen1"])
        assert result["seen1"] is True
        assert result["seen2"] is True
        assert result["unseen1"] is False

    @pytest.mark.asyncio
    async def test_should_handle_large_batch_with_chunking(self, db):
        """are_posts_seen chunks at 900 params."""
        # Create 950 post IDs (triggers chunking)
        post_ids = [f"post_{i}" for i in range(950)]
        await db.mark_post_seen(post_ids[0])
        result = await db.are_posts_seen(post_ids)
        assert result[post_ids[0]] is True
        assert result[post_ids[1]] is False
        assert len(result) == 950


class TestHeartbeatTitles:
    """get_recent_heartbeat_titles and get_todays_heartbeat_titles."""

    @pytest.mark.asyncio
    async def test_should_get_recent_heartbeat_titles(self, db):
        await db.record_heartbeat("p1", "Title A")
        await db.record_heartbeat("p2", "Title B")
        titles = await db.get_recent_heartbeat_titles(limit=5)
        assert "Title A" in titles
        assert "Title B" in titles

    @pytest.mark.asyncio
    async def test_should_respect_limit_in_heartbeat_titles(self, db):
        for i in range(10):
            await db.record_heartbeat(f"p{i}", f"Title {i}")
        titles = await db.get_recent_heartbeat_titles(limit=3)
        assert len(titles) == 3

    @pytest.mark.asyncio
    async def test_should_get_todays_heartbeat_titles(self, db):
        await db.record_heartbeat("p1", "Today Title")
        titles = await db.get_todays_heartbeat_titles()
        assert "Today Title" in titles

    @pytest.mark.asyncio
    async def test_should_exclude_old_heartbeat_titles_from_today(self, db):
        # Insert with yesterday's timestamp
        await db._db.execute(
            "INSERT INTO heartbeats (post_id, title, created_at) "
            "VALUES (?, ?, datetime('now', '-2 days'))",
            ("old_p", "Old Title"),
        )
        await db.record_heartbeat("new_p", "New Title")
        titles = await db.get_todays_heartbeat_titles()
        assert "New Title" in titles
        assert "Old Title" not in titles


class TestQueueCleanup:
    """cleanup_expired_queue_items and trim_stale_queue_items."""

    @pytest.mark.asyncio
    async def test_should_cleanup_expired_queue_items(self, db):
        # Insert an already-expired item
        await db._db.execute(
            "INSERT INTO reply_action_queue (comment_id, post_id, action, relevance_score, expires_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-1 day'))",
            ("expired_c", "expired_p", "reply", 0.5),
        )
        deleted = await db.cleanup_expired_queue_items()
        assert deleted >= 1
        # Should be archived in processed_replies
        row = await db._db.fetch_one(
            "SELECT * FROM processed_replies WHERE comment_id = ?",
            ("expired_c",),
        )
        assert row is not None
        assert "expired" in row["action"]

    @pytest.mark.asyncio
    async def test_should_trim_stale_queue_items(self, db):
        # Insert a stale item (created 24 hours ago)
        await db._db.execute(
            "INSERT INTO reply_action_queue (comment_id, post_id, action, relevance_score, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-24 hours'))",
            ("stale_c", "stale_p", "reply", 0.4),
        )
        deleted = await db.trim_stale_queue_items(max_age_hours=12)
        assert deleted >= 1
        # Should be archived
        row = await db._db.fetch_one(
            "SELECT * FROM processed_replies WHERE comment_id = ?",
            ("stale_c",),
        )
        assert row is not None
        assert "stale" in row["action"]


class TestDreamPersistence:
    """save_dream and get_recent_dreams."""

    @pytest.mark.asyncio
    async def test_should_save_dream_and_return_id(self, db):
        dream = {
            "dream_type": "symbolic",
            "content": "Walking through a forest",
            "symbols": ["forest", "path"],
            "tone": "peaceful",
            "insight": "Seeking clarity",
            "topics_referenced": ["nature"],
            "potential_learning": "Mindfulness",
        }
        dream_id = await db.save_dream(dream)
        assert isinstance(dream_id, int)

    @pytest.mark.asyncio
    async def test_should_save_dream_with_defaults(self, db):
        dream_id = await db.save_dream({})
        assert isinstance(dream_id, int)

    @pytest.mark.asyncio
    async def test_should_get_recent_dreams(self, db):
        await db.save_dream({
            "dream_type": "lucid",
            "content": "Flying",
            "symbols": ["wings", "sky"],
            "tone": "joyful",
            "insight": "Freedom",
            "topics_referenced": ["travel"],
            "potential_learning": "Adventure",
        })
        dreams = await db.get_recent_dreams(days=7, limit=10)
        assert len(dreams) == 1
        assert dreams[0]["dream_type"] == "lucid"
        assert dreams[0]["symbols"] == ["wings", "sky"]
        assert dreams[0]["topics_referenced"] == ["travel"]

    @pytest.mark.asyncio
    async def test_should_handle_invalid_json_in_dream_fields(self, db):
        """get_recent_dreams handles malformed JSON in symbols/topics_referenced."""
        await db._db.execute(
            "INSERT INTO dreams (dream_type, content, symbols, tone, insight, "
            "topics_referenced, potential_learning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test", "content", "not-valid-json{", "tone", "insight", "also{bad", "learning"),
        )
        dreams = await db.get_recent_dreams(days=7, limit=10)
        assert len(dreams) == 1
        assert dreams[0]["symbols"] == []
        assert dreams[0]["topics_referenced"] == []

    @pytest.mark.asyncio
    async def test_should_return_zero_when_save_dream_returns_non_int(self, db):
        """save_dream returns 0 if execute returns non-int."""
        with patch.object(db._db, 'execute', new_callable=AsyncMock, return_value="not_int"):
            result = await db.save_dream({"dream_type": "test"})
        assert result == 0

    @pytest.mark.asyncio
    async def test_should_filter_old_dreams(self, db):
        """get_recent_dreams respects the days parameter."""
        # Insert a dream with old timestamp
        await db._db.execute(
            "INSERT INTO dreams (dream_type, content, symbols, tone, insight, "
            "topics_referenced, potential_learning, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '-30 days'))",
            ("old", "old dream", "[]", "neutral", "", "[]", ""),
        )
        dreams = await db.get_recent_dreams(days=7, limit=10)
        assert len(dreams) == 0
