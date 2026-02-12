"""
Feed processor — polls Moltbook feed and deduplicates items.

Tracks seen post IDs to avoid re-processing. Coordinates with
DecisionEngine and EngagementDB for engagement decisions.
"""

import logging
from typing import Optional

from .models import Post, FeedItem

logger = logging.getLogger(__name__)


class FeedProcessor:
    """
    Processes the Moltbook feed, deduplicating and scoring items.
    """

    def __init__(self, max_seen: int = 5000):
        self._seen_post_ids: set[str] = set()
        self._max_seen = max_seen

    def filter_new_posts(self, posts: list[Post]) -> list[Post]:
        """Filter to only posts we haven't seen yet."""
        new_posts = []
        for post in posts:
            if post.id and post.id not in self._seen_post_ids:
                new_posts.append(post)
                self._seen_post_ids.add(post.id)

        # Evict oldest if set grows too large
        if len(self._seen_post_ids) > self._max_seen:
            excess = len(self._seen_post_ids) - self._max_seen
            # Remove arbitrary items (set has no ordering, but this prevents unbounded growth)
            to_remove = list(self._seen_post_ids)[:excess]
            for item in to_remove:
                self._seen_post_ids.discard(item)

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
                self._seen_post_ids.add(item_id)

        if len(self._seen_post_ids) > self._max_seen:
            excess = len(self._seen_post_ids) - self._max_seen
            to_remove = list(self._seen_post_ids)[:excess]
            for item in to_remove:
                self._seen_post_ids.discard(item)

        return new_items

    def mark_seen(self, post_id: str) -> None:
        """Manually mark a post as seen."""
        self._seen_post_ids.add(post_id)

    def is_seen(self, post_id: str) -> bool:
        """Check if a post has been seen."""
        return post_id in self._seen_post_ids

    @property
    def seen_count(self) -> int:
        """Number of tracked post IDs."""
        return len(self._seen_post_ids)
