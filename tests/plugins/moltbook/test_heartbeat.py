"""
Tests for HeartbeatManager — topic rotation, recording, and state persistence.
"""

import json
from unittest.mock import AsyncMock

import pytest

from overblick.plugins.moltbook.heartbeat import HeartbeatManager


@pytest.fixture
def mock_db():
    """Mock engagement database."""
    db = AsyncMock()
    db.record_heartbeat = AsyncMock()
    return db


@pytest.fixture
def manager(mock_db):
    """Create HeartbeatManager with default settings."""
    return HeartbeatManager(engagement_db=mock_db, topic_count=6)


class TestTopicRotation:
    """Tests for topic index rotation."""

    def test_initial_index_is_zero(self, manager):
        assert manager.get_next_topic_index() == 0

    def test_sequential_indices(self, manager):
        indices = [manager.get_next_topic_index() for _ in range(6)]
        assert indices == [0, 1, 2, 3, 4, 5]

    def test_wraps_around(self, manager):
        for _ in range(6):
            manager.get_next_topic_index()
        assert manager.get_next_topic_index() == 0

    def test_custom_topic_count(self, mock_db):
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=3)
        indices = [mgr.get_next_topic_index() for _ in range(7)]
        assert indices == [0, 1, 2, 0, 1, 2, 0]

    def test_single_topic(self, mock_db):
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=1)
        indices = [mgr.get_next_topic_index() for _ in range(3)]
        assert indices == [0, 0, 0]


class TestRecordHeartbeat:
    """Tests for heartbeat recording."""

    @pytest.mark.asyncio
    async def test_records_to_database(self, manager, mock_db):
        await manager.record_heartbeat("post-123", "Test Post Title")
        mock_db.record_heartbeat.assert_called_once_with("post-123", "Test Post Title")

    @pytest.mark.asyncio
    async def test_long_title_logged(self, manager, mock_db):
        """Long titles are truncated in log messages but stored in full."""
        long_title = "A" * 200
        await manager.record_heartbeat("post-456", long_title)
        mock_db.record_heartbeat.assert_called_once_with("post-456", long_title)


class TestStatePersistence:
    """Tests for topic index persistence across restarts."""

    def test_save_and_load_state(self, mock_db, tmp_path):
        """Topic index survives save/load cycle."""
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=5)
        # Advance to index 3
        for _ in range(3):
            mgr.get_next_topic_index()
        mgr.save_state(tmp_path)

        # New manager loads the state
        mgr2 = HeartbeatManager(engagement_db=mock_db, topic_count=5)
        mgr2.load_state(tmp_path)
        assert mgr2.get_next_topic_index() == 3

    def test_load_state_no_file(self, mock_db, tmp_path):
        """load_state with no file leaves index at 0."""
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=5)
        mgr.load_state(tmp_path)
        assert mgr.get_next_topic_index() == 0

    def test_load_state_topic_count_changed(self, mock_db, tmp_path):
        """Index wraps when topic_count changes between saves."""
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=3)
        # Advance to index 2 (next would be 0)
        mgr._current_topic_index = 5  # Simulate saved high index
        mgr.save_state(tmp_path)

        # New manager with fewer topics
        mgr2 = HeartbeatManager(engagement_db=mock_db, topic_count=3)
        mgr2.load_state(tmp_path)
        assert mgr2.get_next_topic_index() == 2  # 5 % 3 = 2

    def test_save_state_creates_parent_dirs(self, mock_db, tmp_path):
        """save_state creates parent directories if needed."""
        nested = tmp_path / "deep" / "nested"
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=5)
        mgr.get_next_topic_index()  # advance to 1
        mgr.save_state(nested)
        assert (nested / "heartbeat_state.json").exists()

    def test_state_file_format(self, mock_db, tmp_path):
        """State file is valid JSON with expected keys."""
        mgr = HeartbeatManager(engagement_db=mock_db, topic_count=5)
        mgr.get_next_topic_index()  # 0 -> advance to 1
        mgr.save_state(tmp_path)
        data = json.loads((tmp_path / "heartbeat_state.json").read_text())
        assert data["current_topic_index"] == 1
        assert data["topic_count"] == 5
