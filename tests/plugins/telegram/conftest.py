"""
Shared fixtures for Telegram plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from blick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from blick.core.llm.pipeline import PipelineResult, PipelineStage
from blick.core.plugin_base import PluginContext
from blick.plugins.telegram.plugin import TelegramPlugin


# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def telegram_identity():
    """Identity configured for Telegram plugin testing."""
    return Identity(
        name="volt",
        display_name="Volt",
        description="Punk tech critic on Telegram",
        engagement_threshold=30,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["privacy", "surveillance", "open source"],
        raw_config={
            "agent_name": "Volt",
            "telegram": {
                "allowed_chat_ids": [],
                "rate_limit_per_minute": 10,
                "rate_limit_per_hour": 60,
            },
        },
    )


# ---------------------------------------------------------------------------
# Mock LLM pipeline
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_pipeline():
    """Mock SafeLLMPipeline that returns configurable responses."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="Test response from Volt.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


# ---------------------------------------------------------------------------
# PluginContext
# ---------------------------------------------------------------------------

@pytest.fixture
def telegram_context(telegram_identity, tmp_path, mock_llm_client, mock_audit_log, mock_llm_pipeline):
    """PluginContext wired for Telegram plugin."""
    ctx = PluginContext(
        identity_name=telegram_identity.name,
        data_dir=tmp_path / "data" / "volt",
        log_dir=tmp_path / "logs" / "volt",
        llm_client=mock_llm_client,
        llm_pipeline=mock_llm_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=telegram_identity,
    )
    ctx._secrets_getter = lambda key: {
        "telegram_bot_token": "test-bot-token-123",
    }.get(key)
    return ctx


# ---------------------------------------------------------------------------
# Plugin fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def telegram_plugin(telegram_context):
    """Set up TelegramPlugin with mocked context."""
    plugin = TelegramPlugin(telegram_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()


# ---------------------------------------------------------------------------
# Telegram update factories
# ---------------------------------------------------------------------------

def make_update(
    text: str,
    chat_id: int = 12345,
    user_id: int = 67890,
    username: str = "testuser",
    message_id: int = 1,
    update_id: int = 100,
) -> dict:
    """Factory for creating Telegram update dicts."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": username, "first_name": "Test"},
            "text": text,
            "date": 1700000000,
        },
    }
