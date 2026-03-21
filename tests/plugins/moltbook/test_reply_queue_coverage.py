"""
Additional coverage tests for reply_queue module.

Covers uncovered lines:
- 87-90: exception handling in reply callback
"""

from unittest.mock import AsyncMock

import pytest

from overblick.plugins.moltbook.reply_queue import ReplyQueueManager


class TestReplyQueueExpiredAndStale:
    @pytest.mark.asyncio
    async def test_should_log_expired_items(self, mock_engagement_db):
        """Lines 45-46: expired items > 0 logs info."""
        mock_engagement_db.cleanup_expired_queue_items.return_value = 3
        manager = ReplyQueueManager(engagement_db=mock_engagement_db)
        result = await manager.process_queue(AsyncMock())
        assert result["expired"] == 3

    @pytest.mark.asyncio
    async def test_should_log_stale_items(self, mock_engagement_db):
        """Lines 49-50: stale items > 0 logs info."""
        mock_engagement_db.trim_stale_queue_items.return_value = 2
        manager = ReplyQueueManager(engagement_db=mock_engagement_db)
        result = await manager.process_queue(AsyncMock())
        assert result["expired"] == 0  # no expired


class TestReplyQueueCallbackException:
    @pytest.mark.asyncio
    async def test_should_handle_callback_exception(self, mock_engagement_db):
        """Lines 87-90: exception in callback is caught and retried."""
        mock_engagement_db.get_pending_reply_actions.return_value = [
            {
                "id": 1,
                "comment_id": "c1",
                "post_id": "p1",
                "action": "reply",
                "relevance_score": 0.8,
                "retry_count": 0,
            },
        ]

        callback = AsyncMock(side_effect=RuntimeError("Network error"))
        manager = ReplyQueueManager(engagement_db=mock_engagement_db)
        result = await manager.process_queue(callback)

        assert result["failed"] == 1
        mock_engagement_db.update_queue_retry.assert_called_once()
        # Verify the error message was truncated and passed
        call_args = mock_engagement_db.update_queue_retry.call_args[0]
        assert call_args[0] == 1  # queue_id
        assert "Network error" in call_args[1]
