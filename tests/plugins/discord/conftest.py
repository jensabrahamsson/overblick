"""
Shared fixtures for Discord plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.llm.pipeline import PipelineResult
from overblick.core.plugin_base import PluginContext
from overblick.plugins.discord.plugin import DiscordPlugin


@pytest.fixture
def discord_identity():
    """Identity configured for Discord plugin testing."""
    return Identity(
        name="blixt",
        display_name="Blixt",
        description="Punk tech critic on Discord",
        engagement_threshold=30,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["privacy", "open source", "decentralization"],
        raw_config={
            "agent_name": "Blixt",
            "discord": {
                "guild_ids": [111111, 222222],
                "channel_ids": [333333, 444444],
            },
        },
    )


@pytest.fixture
def discord_pipeline():
    """Mock SafeLLMPipeline for Discord tests."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="Test response from Volt on Discord.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


@pytest.fixture
def discord_context(discord_identity, tmp_path, mock_llm_client, mock_audit_log, discord_pipeline):
    """PluginContext wired for Discord plugin."""
    ctx = PluginContext(
        identity_name=discord_identity.name,
        data_dir=tmp_path / "data" / "volt",
        log_dir=tmp_path / "logs" / "volt",
        llm_client=mock_llm_client,
        llm_pipeline=discord_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=discord_identity,
    )
    ctx._secrets_getter = lambda key: {
        "discord_bot_token": "test-discord-token-abc",
    }.get(key)
    return ctx


@pytest.fixture
async def discord_plugin(discord_context):
    """Set up DiscordPlugin with mocked context."""
    plugin = DiscordPlugin(discord_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()
