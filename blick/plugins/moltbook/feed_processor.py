"""
Feed processor — polls Moltbook feed and deduplicates items.

Tracks seen post IDs to avoid re-processing. Uses a deque alongside
a set for O(1) lookup + guaranteed FIFO eviction order.
"""

import logging
from collections import deque
from typing import Optional

from .models import Post, FeedItem

logger = logging.getLogger(__name__)


class FeedProcessor:
    """
    Processes the Moltbook feed, deduplicating and scoring items.

    Memory-bounded: evicts oldest seen IDs when max_seen is exceeded.
    """

    def __init__(self, max_seen: int = 5000):
        self._seen_post_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._max_seen = max_seen

    def _track_seen(self, item_id: str) -> None:
        """Track an item as seen, evicting oldest if over capacity."""
        self._seen_post_ids.add(item_id)
        self._seen_order.append(item_id)

        # Evict oldest items (FIFO) to stay within memory bounds
        while len(self._seen_post_ids) > self._max_seen:
            oldest = self._seen_order.popleft()
            self._seen_post_ids.discard(oldest)

    def filter_new_posts(self, posts: list[Post]) -> list[Post]:
        """Filter to only posts we haven't seen yet."""
        new_posts = []
        for post in posts:
            if post.id and post.id not in self._seen_post_ids:
                new_posts.append(post)
                self._track_seen(post.id)

        if new_posts:
            logger.debug("Feed: %d new posts (of %d total)", len(new_posts), len(posts))

        return new_posts

    def filter_new_feed_items(self, items: list[FeedItem]) -> list[FeedItem]:
        """Filter to only feed items we haven't seen yet."""
        new_items = []
        for item in items:
            item_id = item.post_id or item.id
            if item_id and item_id not in self._seen_post_ids:
                new_items.append(item)
                self._track_seen(item_id)

        return new_items

    def mark_seen(self, post_id: str) -> None:
        """Manually mark a post as seen."""
        if post_id not in self._seen_post_ids:
            self._track_seen(post_id)

    def is_seen(self, post_id: str) -> bool:
        """Check if a post has been seen."""
        return post_id in self._seen_post_ids

    @property
    def seen_count(self) -> int:
        """Number of tracked post IDs."""
        return len(self._seen_post_ids)
