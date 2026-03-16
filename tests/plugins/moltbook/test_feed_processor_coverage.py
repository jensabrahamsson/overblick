"""
Additional coverage tests for feed_processor module.

Covers uncovered lines:
- 54-61: filter_new_feed_items method
"""

from unittest.mock import MagicMock

from overblick.plugins.moltbook.feed_processor import FeedProcessor


class TestFilterNewFeedItems:
    def test_should_filter_new_feed_items(self):
        """Lines 54-61: filter_new_feed_items processes FeedItem objects."""
        fp = FeedProcessor()

        item1 = MagicMock()
        item1.post_id = "p1"
        item1.id = "i1"

        item2 = MagicMock()
        item2.post_id = "p2"
        item2.id = "i2"

        new = fp.filter_new_feed_items([item1, item2])
        assert len(new) == 2
        assert fp.is_seen("p1")
        assert fp.is_seen("p2")

    def test_should_skip_already_seen_feed_items(self):
        """Already seen feed items are filtered out."""
        fp = FeedProcessor()

        item1 = MagicMock()
        item1.post_id = "p1"
        item1.id = "i1"

        fp.filter_new_feed_items([item1])
        new = fp.filter_new_feed_items([item1])
        assert len(new) == 0

    def test_should_use_id_when_post_id_is_none(self):
        """Line 56: falls back to item.id when post_id is None/empty."""
        fp = FeedProcessor()

        item = MagicMock()
        item.post_id = ""
        item.id = "fallback-id"

        new = fp.filter_new_feed_items([item])
        assert len(new) == 1
        assert fp.is_seen("fallback-id")

    def test_should_skip_items_with_no_id(self):
        """Items with both post_id and id empty/None are skipped."""
        fp = FeedProcessor()

        item = MagicMock()
        item.post_id = ""
        item.id = ""

        new = fp.filter_new_feed_items([item])
        assert len(new) == 0
