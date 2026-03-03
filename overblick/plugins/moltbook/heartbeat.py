"""
Heartbeat posting manager.

Handles scheduled heartbeat posts with rotating topics
and engagement database tracking. Topic index persists
across restarts via a JSON state file.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """
    Manages heartbeat posting schedule and topic rotation.

    The topic index is persisted to ``<data_dir>/heartbeat_state.json``
    so that topic rotation survives agent restarts.
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

    async def record_heartbeat(self, post_id: str, title: str) -> None:
        """Record a heartbeat post in the engagement database."""
        await self._db.record_heartbeat(post_id, title)
        logger.info("Heartbeat recorded: %s (%s)", title[:50], post_id)

    def save_state(self, data_dir: Path) -> None:
        """Persist current topic index to disk."""
        try:
            state_file = data_dir / "heartbeat_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps(
                    {
                        "current_topic_index": self._current_topic_index,
                        "topic_count": self._topic_count,
                    }
                )
            )
        except Exception as e:
            logger.debug("Failed to persist heartbeat state: %s", e)

    def load_state(self, data_dir: Path) -> None:
        """Restore topic index from disk."""
        try:
            state_file = data_dir / "heartbeat_state.json"
            if not state_file.exists():
                return
            data = json.loads(state_file.read_text())
            saved_index = data.get("current_topic_index", 0)
            saved_count = data.get("topic_count", self._topic_count)
            # If topic_count changed (topics added/removed), wrap the index
            self._current_topic_index = saved_index % self._topic_count
            if saved_count != self._topic_count:
                logger.info(
                    "Topic count changed (%d -> %d), wrapped index to %d",
                    saved_count,
                    self._topic_count,
                    self._current_topic_index,
                )
        except Exception as e:
            logger.debug("Failed to load heartbeat state: %s", e)
