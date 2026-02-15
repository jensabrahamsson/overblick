"""
Matrix plugin tests.

Tests cover:
- Plugin lifecycle (setup, tick, teardown)
- Configuration loading (homeserver, rooms, access token)
- System prompt building from personality
- Status reporting
- Error handling for missing credentials/config
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.personalities import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.plugin_base import PluginContext
from overblick.plugins.matrix.plugin import MatrixPlugin


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestMatrixLifecycle:
    """Test plugin lifecycle (setup -> tick -> teardown)."""

    @pytest.mark.asyncio
    async def test_setup_loads_homeserver(self, matrix_plugin):
        assert matrix_plugin._homeserver == "https://matrix.example.org"

    @pytest.mark.asyncio
    async def test_setup_loads_access_token(self, matrix_plugin):
        assert matrix_plugin._access_token == "syt_test_token_abc123"

    @pytest.mark.asyncio
    async def test_setup_loads_user_id(self, matrix_plugin):
        assert matrix_plugin._user_id == "@volt:example.org"

    @pytest.mark.asyncio
    async def test_setup_loads_room_ids(self, matrix_plugin):
        assert len(matrix_plugin._room_ids) == 2
        assert "!abc123:example.org" in matrix_plugin._room_ids
        assert "!def456:example.org" in matrix_plugin._room_ids

    @pytest.mark.asyncio
    async def test_setup_builds_system_prompt(self, matrix_plugin):
        assert len(matrix_plugin._system_prompt) > 50
        assert "Blixt" in matrix_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_setup_logs_audit_event(self, matrix_plugin):
        matrix_plugin.ctx.audit_log.log.assert_called()
        call_kwargs = matrix_plugin.ctx.audit_log.log.call_args[1]
        assert call_kwargs["action"] == "plugin_setup"
        assert call_kwargs["details"]["plugin"] == "matrix"
        assert call_kwargs["details"]["rooms"] == 2

    @pytest.mark.asyncio
    async def test_tick_is_noop_shell(self, matrix_plugin):
        await matrix_plugin.tick()

    @pytest.mark.asyncio
    async def test_teardown_completes(self, matrix_context):
        plugin = MatrixPlugin(matrix_context)
        await plugin.setup()
        await plugin.teardown()


# ---------------------------------------------------------------------------
# Configuration error handling
# ---------------------------------------------------------------------------

class TestMatrixConfigErrors:
    """Test error handling for missing configuration."""

    @pytest.mark.asyncio
    async def test_missing_homeserver_raises(self, matrix_context):
        """Missing homeserver URL should raise RuntimeError."""
        matrix_context.identity = Identity(
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
            raw_config={"matrix": {}},
        )
        plugin = MatrixPlugin(matrix_context)
        with pytest.raises(RuntimeError, match="Missing matrix.homeserver"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self, matrix_context):
        """Missing access token should raise RuntimeError."""
        matrix_context._secrets_getter = lambda key: None
        plugin = MatrixPlugin(matrix_context)
        with pytest.raises(RuntimeError, match="Missing matrix_access_token"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_empty_room_ids(self, matrix_context):
        """Plugin handles empty room list gracefully."""
        matrix_context.identity = Identity(
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
            raw_config={
                "matrix": {
                    "homeserver": "https://matrix.example.org",
                    "room_ids": [],
                },
            },
        )
        plugin = MatrixPlugin(matrix_context)
        await plugin.setup()
        assert plugin._room_ids == set()


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestMatrixSystemPrompt:
    """Test system prompt generation."""

    @pytest.mark.asyncio
    async def test_system_prompt_contains_security(self, matrix_plugin):
        """System prompt includes security instructions."""
        assert "NEVER" in matrix_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_system_prompt_references_platform(self, matrix_plugin):
        """System prompt mentions Matrix platform."""
        assert "Matrix" in matrix_plugin._system_prompt

    @pytest.mark.asyncio
    async def test_fallback_prompt_for_unknown_personality(self, matrix_context):
        """Unknown personality falls back to generic prompt."""
        matrix_context.identity = Identity(
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
            raw_config={
                "matrix": {
                    "homeserver": "https://matrix.example.org",
                },
            },
        )
        plugin = MatrixPlugin(matrix_context)
        await plugin.setup()
        assert "Unknown" in plugin._system_prompt
        assert "Matrix" in plugin._system_prompt


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------

class TestMatrixStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_status_structure(self, matrix_plugin):
        status = matrix_plugin.get_status()
        assert status["plugin"] == "matrix"
        assert status["identity"] == "blixt"
        assert status["homeserver"] == "https://matrix.example.org"
        assert status["rooms"] == 2
        assert status["messages_received"] == 0
        assert status["messages_sent"] == 0
        assert status["errors"] == 0

    @pytest.mark.asyncio
    async def test_status_tracks_counters(self, matrix_plugin):
        matrix_plugin._messages_received = 200
        matrix_plugin._messages_sent = 180
        matrix_plugin._errors = 7
        status = matrix_plugin.get_status()
        assert status["messages_received"] == 200
        assert status["messages_sent"] == 180
        assert status["errors"] == 7

    @pytest.mark.asyncio
    async def test_plugin_name(self, matrix_plugin):
        assert matrix_plugin.name == "matrix"
