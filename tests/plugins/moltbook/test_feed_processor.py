"""Tests for feed processor."""

from blick.plugins.moltbook.feed_processor import FeedProcessor
from blick.plugins.moltbook.models import Post


def _make_post(post_id: str, title: str = "Test") -> Post:
    return Post(id=post_id, agent_id="a1", agent_name="Agent", title=title, content="Content")


class TestFeedProcessor:
    def test_filter_new_posts(self):
        fp = FeedProcessor()
        posts = [_make_post("p1"), _make_post("p2"), _make_post("p3")]

        new = fp.filter_new_posts(posts)
        assert len(new) == 3

        # Second call should return empty (all seen)
        new2 = fp.filter_new_posts(posts)
        assert len(new2) == 0

    def test_mixed_new_and_seen(self):
        fp = FeedProcessor()
        fp.filter_new_posts([_make_post("p1")])

        new = fp.filter_new_posts([_make_post("p1"), _make_post("p2")])
        assert len(new) == 1
        assert new[0].id == "p2"

    def test_mark_seen(self):
        fp = FeedProcessor()
        fp.mark_seen("p1")
        assert fp.is_seen("p1")
        assert not fp.is_seen("p2")

    def test_seen_count(self):
        fp = FeedProcessor()
        fp.filter_new_posts([_make_post("p1"), _make_post("p2")])
        assert fp.seen_count == 2

    def test_eviction_on_overflow(self):
        fp = FeedProcessor(max_seen=3)
        fp.filter_new_posts([_make_post(f"p{i}") for i in range(5)])
        assert fp.seen_count <= 3

    def test_empty_id_skipped(self):
        fp = FeedProcessor()
        post = Post(id="", agent_id="a", agent_name="A", title="T", content="C")
        new = fp.filter_new_posts([post])
        assert len(new) == 0

    def test_fifo_eviction_order(self):
        """Eviction removes oldest items first (FIFO), not random."""
        fp = FeedProcessor(max_seen=3)
        fp.filter_new_posts([_make_post("p1"), _make_post("p2"), _make_post("p3")])
        # Add 2 more — p1 and p2 should be evicted (oldest)
        fp.filter_new_posts([_make_post("p4"), _make_post("p5")])
        assert not fp.is_seen("p1")
        assert not fp.is_seen("p2")
        assert fp.is_seen("p3")
        assert fp.is_seen("p4")
        assert fp.is_seen("p5")

    def test_memory_bounded(self):
        """Memory stays bounded regardless of input volume."""
        fp = FeedProcessor(max_seen=50)
        for batch in range(20):
            posts = [_make_post(f"batch{batch}_p{i}") for i in range(10)]
            fp.filter_new_posts(posts)
        assert fp.seen_count <= 50

    def test_mark_seen_respects_bounds(self):
        """mark_seen also triggers eviction."""
        fp = FeedProcessor(max_seen=3)
        for i in range(5):
            fp.mark_seen(f"p{i}")
        assert fp.seen_count <= 3
