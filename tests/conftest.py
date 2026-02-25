"""
Shared test fixtures for Överblick tests.
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
    """Mock engagement database (async — mirrors EngagementDB's async API)."""
    db = AsyncMock()
    db.record_engagement = AsyncMock()
    db.record_heartbeat = AsyncMock()
    db.is_reply_processed = AsyncMock(return_value=False)
    db.mark_reply_processed = AsyncMock()
    db.queue_reply_action = AsyncMock()
    db.get_pending_reply_actions = AsyncMock(return_value=[])
    db.remove_from_queue = AsyncMock()
    db.update_queue_retry = AsyncMock()
    db.cleanup_expired_queue_items = AsyncMock(return_value=0)
    db.trim_stale_queue_items = AsyncMock(return_value=0)
    db.track_my_post = AsyncMock()
    db.track_my_comment = AsyncMock()
    db.get_my_post_ids = AsyncMock(return_value=[])
    return db


@pytest.fixture
def mock_quiet_hours_checker():
    """Mock quiet hours checker (not quiet by default)."""
    checker = MagicMock()
    checker.is_quiet_hours = MagicMock(return_value=False)
    return checker


@pytest.fixture
def mock_preflight_checker():
    """Mock preflight checker (passes by default)."""
    checker = AsyncMock()
    checker.check = AsyncMock(return_value=True)
    return checker


@pytest.fixture
def mock_output_safety():
    """Mock output safety (returns input unchanged)."""
    safety = AsyncMock()
    safety.check = AsyncMock(side_effect=lambda text: text)
    return safety
