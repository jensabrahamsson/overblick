"""Tests for reply queue manager."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from blick.plugins.moltbook.reply_queue import ReplyQueueManager


@pytest.mark.asyncio
async def test_process_empty_queue(mock_engagement_db):
    manager = ReplyQueueManager(engagement_db=mock_engagement_db)
    result = await manager.process_queue(AsyncMock())

    assert result["processed"] == 0
    assert result["success"] == 0


@pytest.mark.asyncio
async def test_process_queue_success(mock_engagement_db):
    mock_engagement_db.get_pending_reply_actions.return_value = [
        {"id": 1, "comment_id": "c1", "post_id": "p1", "action": "reply",
         "relevance_score": 0.8, "retry_count": 0},
    ]

    callback = AsyncMock(return_value=True)
    manager = ReplyQueueManager(engagement_db=mock_engagement_db)
    result = await manager.process_queue(callback)

    assert result["processed"] == 1
    assert result["success"] == 1
    mock_engagement_db.mark_reply_processed.assert_called_once()
    mock_engagement_db.remove_from_queue.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_process_queue_failure(mock_engagement_db):
    mock_engagement_db.get_pending_reply_actions.return_value = [
        {"id": 1, "comment_id": "c1", "post_id": "p1", "action": "reply",
         "relevance_score": 0.8, "retry_count": 0},
    ]

    callback = AsyncMock(return_value=False)
    manager = ReplyQueueManager(engagement_db=mock_engagement_db)
    result = await manager.process_queue(callback)

    assert result["failed"] == 1
    mock_engagement_db.update_queue_retry.assert_called_once()


@pytest.mark.asyncio
async def test_max_retries_exceeded(mock_engagement_db):
    mock_engagement_db.get_pending_reply_actions.return_value = [
        {"id": 1, "comment_id": "c1", "post_id": "p1", "action": "reply",
         "relevance_score": 0.8, "retry_count": 5},
    ]

    manager = ReplyQueueManager(engagement_db=mock_engagement_db, max_retries=3)
    result = await manager.process_queue(AsyncMock())

    mock_engagement_db.mark_reply_processed.assert_called_once()
    mock_engagement_db.remove_from_queue.assert_called_once()
