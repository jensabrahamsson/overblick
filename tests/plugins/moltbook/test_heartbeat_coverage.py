"""
Additional coverage tests for heartbeat module.

Covers uncovered lines:
- 58-59: save_state exception handling
- 73-80: load_state exception handling and topic_count changed path
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.moltbook.heartbeat import HeartbeatManager


class TestSaveStateException:
    def test_should_handle_save_state_exception(self, tmp_path):
        """Lines 58-59: save_state catches exceptions gracefully."""
        db = AsyncMock()
        mgr = HeartbeatManager(engagement_db=db, topic_count=5)

        # Use a path that will cause an OS error
        with patch.object(
            type(tmp_path / "heartbeat_state.json"),
            "write_text",
            side_effect=OSError("Permission denied"),
        ):
            # Should not raise
            mgr.save_state(tmp_path)


class TestLoadStateException:
    def test_should_handle_load_state_exception(self, tmp_path):
        """Lines 79-80: load_state catches exceptions gracefully."""
        db = AsyncMock()
        mgr = HeartbeatManager(engagement_db=db, topic_count=5)

        # Write invalid JSON to state file
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text("not valid json{{{")

        # Should not raise
        mgr.load_state(tmp_path)
        # Index should remain at 0
        assert mgr.get_next_topic_index() == 0

    def test_should_log_when_topic_count_unchanged(self, tmp_path):
        """Lines 72-78: when saved_count != topic_count, wraps and logs."""
        db = AsyncMock()
        mgr = HeartbeatManager(engagement_db=db, topic_count=4)

        # Save state with different topic count
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(json.dumps({
            "current_topic_index": 7,
            "topic_count": 10,  # Different from 4
        }))

        mgr.load_state(tmp_path)
        # 7 % 4 = 3
        assert mgr.get_next_topic_index() == 3
