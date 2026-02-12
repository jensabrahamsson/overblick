"""
Shared test fixtures for Blick tests.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns configurable responses."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"content": "Test response"})
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_audit_log():
    """Mock audit log."""
    log = MagicMock()
    log.log = MagicMock()
    log.query = MagicMock(return_value=[])
    return log


@pytest.fixture
def mock_engagement_db():
    """Mock engagement database."""
    db = MagicMock()
    db.record_engagement = MagicMock()
    db.record_heartbeat = MagicMock()
    db.is_reply_processed = MagicMock(return_value=False)
    db.mark_reply_processed = MagicMock()
    db.queue_reply_action = MagicMock()
    db.get_pending_reply_actions = MagicMock(return_value=[])
    db.remove_from_queue = MagicMock()
    db.update_queue_retry = MagicMock()
    db.cleanup_expired_queue_items = MagicMock(return_value=0)
    db.trim_stale_queue_items = MagicMock(return_value=0)
    db.track_my_post = MagicMock()
    db.track_my_comment = MagicMock()
    db.get_my_post_ids = MagicMock(return_value=[])
    return db
