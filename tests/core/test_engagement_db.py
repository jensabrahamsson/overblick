"""Tests for engagement database."""

import pytest
from blick.core.db.engagement_db import EngagementDB


class TestEngagementDB:
    def test_record_engagement(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.record_engagement("post1", "comment", 0.85)
        # Should not raise

    def test_record_heartbeat(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.record_heartbeat("post1", "Test Title")

    def test_track_my_post(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.track_my_post("post1", "My Post")
        ids = db.get_my_post_ids()
        assert "post1" in ids

    def test_track_my_comment(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.track_my_comment("comment1", "post1")

    def test_reply_processing(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")

        assert not db.is_reply_processed("comment1")
        db.mark_reply_processed("comment1", "post1", "comment", 0.7)
        assert db.is_reply_processed("comment1")

    def test_reply_queue(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")

        db.queue_reply_action("comment1", "post1", "reply", 0.9)
        pending = db.get_pending_reply_actions()
        assert len(pending) == 1
        assert pending[0]["comment_id"] == "comment1"

        db.remove_from_queue(pending[0]["id"])
        assert len(db.get_pending_reply_actions()) == 0

    def test_queue_retry(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.queue_reply_action("comment1", "post1", "reply", 0.9)
        pending = db.get_pending_reply_actions()
        db.update_queue_retry(pending[0]["id"], "test error")

        updated = db.get_pending_reply_actions()
        assert updated[0]["retry_count"] == 1

    def test_is_reply_processed_checks_queue(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.queue_reply_action("comment_q", "post1", "reply", 0.5)
        assert db.is_reply_processed("comment_q")

    def test_untrack_my_post(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        db.track_my_post("post1", "Title")
        db.untrack_my_post("post1")
        ids = db.get_my_post_ids()
        assert "post1" not in ids

    def test_cleanup_expired(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        # Just verify it doesn't crash
        count = db.cleanup_expired_queue_items()
        assert count == 0

    def test_trim_stale(self, tmp_path):
        db = EngagementDB(tmp_path / "test.db")
        count = db.trim_stale_queue_items(max_age_hours=0)
        assert isinstance(count, int)
