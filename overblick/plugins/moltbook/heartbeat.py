"""
Heartbeat posting manager.

Handles scheduled heartbeat posts with rotating topics
and engagement database tracking.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """
    Manages heartbeat posting schedule and topic rotation.
    """

    def __init__(
        self,
        engagement_db,
        topic_count: int = 6,
    ):
        self._db = engagement_db
        self._topic_count = topic_count
        self._current_topic_index = 0

    def get_next_topic_index(self) -> int:
        """Get the next topic index (rotating)."""
        idx = self._current_topic_index
        self._current_topic_index = (self._current_topic_index + 1) % self._topic_count
        return idx

    def record_heartbeat(self, post_id: str, title: str) -> None:
        """Record a heartbeat post in the engagement database."""
        self._db.record_heartbeat(post_id, title)
        logger.info("Heartbeat recorded: %s (%s)", title[:50], post_id)
