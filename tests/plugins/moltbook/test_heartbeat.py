"""
Tests for HeartbeatManager — topic rotation and recording.
"""

import pytest
from unittest.mock import AsyncMock

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
