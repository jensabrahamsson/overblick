"""
Discord plugin tests.

Tests cover:
- Plugin lifecycle (setup, tick, teardown)
- Configuration loading (guilds, channels, bot token)
- System prompt building from personality
- Status reporting
- Error handling for missing credentials
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.identities import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.plugin_base import PluginContext
from overblick.plugins.discord.plugin import DiscordPlugin


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestDiscordLifecycle:
    """Test plugin lifecycle (setup → tick → teardown)."""

    @pytest.mark.asyncio
    async def test_setup_loads_bot_token(self, discord_plugin):
        assert discord_plugin._bot_token == "test-discord-token-abc"

    @pytest.mark.asyncio
    async def test_setup_loads_guild_ids(self, discord_plugin):
        assert discord_plugin._guild_ids == {111111, 222222}

    @pytest.mark.asyncio
    async def test_setup_loads_channel_ids(self, discord_plugin):
        assert discord_plugin._channel_ids == {333333, 444444}

    @pytest.mark.asyncio
    async def test_setup_builds_system_prompt(self, discord_plugin):
        assert len(discord_plugin._system_prompt) > 50
        assert "Blixt" in discord_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_setup_logs_audit_event(self, discord_plugin):
        discord_plugin.ctx.audit_log.log.assert_called()
        call_kwargs = discord_plugin.ctx.audit_log.log.call_args[1]
        assert call_kwargs["action"] == "plugin_setup"
        assert call_kwargs["details"]["plugin"] == "discord"

    @pytest.mark.asyncio
    async def test_tick_is_noop_shell(self, discord_plugin):
        """Shell tick() does nothing — just verifies it doesn't crash."""
        await discord_plugin.tick()

    @pytest.mark.asyncio
    async def test_teardown_completes(self, discord_context):
        plugin = DiscordPlugin(discord_context)
        await plugin.setup()
        await plugin.teardown()
        # No exception = success


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestDiscordConfig:
    """Test configuration handling."""

    @pytest.mark.asyncio
    async def test_empty_guild_ids(self, discord_context):
        """Plugin handles empty guild list gracefully."""
        discord_context.identity = Identity(
            name="blixt",
            display_name="Blixt",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={"discord": {}},
        )
        plugin = DiscordPlugin(discord_context)
        await plugin.setup()
        assert plugin._guild_ids == set()
        assert plugin._channel_ids == set()

    @pytest.mark.asyncio
    async def test_no_discord_config_section(self, discord_context):
        """Plugin handles missing discord config section."""
        discord_context.identity = Identity(
            name="blixt",
            display_name="Blixt",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={},
        )
        plugin = DiscordPlugin(discord_context)
        await plugin.setup()
        assert plugin._guild_ids == set()

    @pytest.mark.asyncio
    async def test_missing_bot_token_raises(self, discord_context):
        """Missing bot token should raise RuntimeError."""
        discord_context._secrets_getter = lambda key: None
        plugin = DiscordPlugin(discord_context)
        with pytest.raises(RuntimeError, match="Missing discord_bot_token"):
            await plugin.setup()


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestDiscordSystemPrompt:
    """Test system prompt generation."""

    @pytest.mark.asyncio
    async def test_system_prompt_contains_security_section(self, discord_plugin):
        """System prompt from personality includes security instructions."""
        assert "NEVER" in discord_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_system_prompt_references_platform(self, discord_plugin):
        """System prompt mentions Discord platform."""
        assert "Discord" in discord_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_fallback_prompt_for_unknown_personality(self, discord_context):
        """Unknown personality falls back to generic prompt."""
        discord_context.identity = Identity(
            name="unknown_agent",
            display_name="Unknown",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={},
        )
        plugin = DiscordPlugin(discord_context)
        await plugin.setup()
        assert "Unknown" in plugin._system_prompt
        assert "Discord" in plugin._system_prompt


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------

class TestDiscordStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_status_structure(self, discord_plugin):
        status = discord_plugin.get_status()
        assert status["plugin"] == "discord"
        assert status["identity"] == "blixt"
        assert status["guilds"] == 2
        assert status["messages_received"] == 0
        assert status["messages_sent"] == 0
        assert status["errors"] == 0

    @pytest.mark.asyncio
    async def test_status_tracks_counters(self, discord_plugin):
        discord_plugin._messages_received = 42
        discord_plugin._messages_sent = 38
        discord_plugin._errors = 3
        status = discord_plugin.get_status()
        assert status["messages_received"] == 42
        assert status["messages_sent"] == 38
        assert status["errors"] == 3

    @pytest.mark.asyncio
    async def test_plugin_name(self, discord_plugin):
        assert discord_plugin.name == "discord"
