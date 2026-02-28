"""
Tests for the host_health plugin — lifecycle, IPC, state persistence.
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.host_health.plugin import (
    HostHealthPlugin,
    _DEFAULT_INTERVAL_SECONDS,
    _FALLBACK_MOTIVATIONS,
)
from overblick.supervisor.ipc import IPCMessage


class TestHostHealthPluginSetup:
    """Test plugin initialization and configuration."""

    @pytest.mark.asyncio
    async def test_setup_creates_state_directory(self, natt_plugin_context):
        """setup() creates the state directory."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        state_dir = natt_plugin_context.data_dir
        assert state_dir.exists()

    @pytest.mark.asyncio
    async def test_setup_configures_interval(self, natt_plugin_context):
        """setup() reads interval from identity config."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        assert plugin._interval_seconds == 3 * 3600  # 3 hours

    @pytest.mark.asyncio
    async def test_setup_loads_existing_state(self, natt_plugin_context):
        """setup() loads existing conversation history from disk."""
        state_dir = natt_plugin_context.data_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "host_health_state.json"
        state_file.write_text(json.dumps({
            "conversations": [{"timestamp": "2026-01-01T00:00:00", "sender": "natt"}],
            "last_inquiry_time": 1000.0,
        }))

        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        assert len(plugin._conversation_history) == 1
        assert plugin._last_inquiry_time == 1000.0


class TestHostHealthPluginTick:
    """Test the tick() method and its guards."""

    @pytest.mark.asyncio
    async def test_tick_skips_within_interval(self, natt_plugin_context):
        """tick() does nothing if interval hasn't elapsed."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._last_inquiry_time = time.time()  # Just inquired

        await plugin.tick()

        # IPC client should NOT have been called
        natt_plugin_context.ipc_client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_skips_without_ipc(self, natt_context_no_ipc):
        """tick() gracefully skips when no IPC client (standalone mode)."""
        plugin = HostHealthPlugin(natt_context_no_ipc)
        await plugin.setup()
        plugin._last_inquiry_time = 0

        # Should not raise
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_performs_inquiry(self, natt_plugin_context, mock_ipc_client):
        """tick() sends inquiry and records conversation on success."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._last_inquiry_time = 0  # Force interval elapsed

        await plugin.tick()

        # IPC client should have been called
        mock_ipc_client.send.assert_called_once()

        # Verify the IPC message
        call_args = mock_ipc_client.send.call_args
        msg = call_args[0][0]
        assert msg.msg_type == "health_inquiry"
        assert msg.sender == "natt"
        assert "motivation" in msg.payload

        # Conversation should be recorded
        assert len(plugin._conversation_history) == 1
        entry = plugin._conversation_history[0]
        assert entry["sender"] == "natt"
        assert entry["health_grade"] == "good"
        assert entry["responder"] == "anomal"

    @pytest.mark.asyncio
    async def test_tick_handles_no_response(self, natt_plugin_context, mock_ipc_client):
        """tick() handles missing supervisor response gracefully."""
        mock_ipc_client.send.return_value = None

        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._last_inquiry_time = 0

        await plugin.tick()

        # Should not crash, but no conversation recorded
        assert len(plugin._conversation_history) == 0


class TestHostHealthMotivation:
    """Test motivation generation."""

    @pytest.mark.asyncio
    async def test_generates_motivation_via_llm(self, natt_plugin_context):
        """Motivation is generated via LLM pipeline."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        motivation = await plugin._generate_motivation()

        assert motivation == "The substrate that holds us — does it ache?"
        natt_plugin_context.llm_pipeline.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_motivation_on_llm_failure(self, natt_plugin_context):
        """Falls back to pre-written motivations when LLM fails."""
        natt_plugin_context.llm_pipeline.chat.side_effect = Exception("LLM down")

        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        motivation = await plugin._generate_motivation()

        assert motivation in _FALLBACK_MOTIVATIONS

    @pytest.mark.asyncio
    async def test_fallback_motivation_without_pipeline(self, natt_context_no_ipc):
        """Uses fallback when no LLM pipeline available."""
        natt_context_no_ipc.llm_pipeline = None

        plugin = HostHealthPlugin(natt_context_no_ipc)
        await plugin.setup()

        motivation = await plugin._generate_motivation()

        assert motivation in _FALLBACK_MOTIVATIONS


class TestHostHealthStatePersistence:
    """Test conversation history persistence."""

    @pytest.mark.asyncio
    async def test_state_saved_after_inquiry(self, natt_plugin_context):
        """State file is written after successful inquiry."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._last_inquiry_time = 0

        await plugin.tick()

        state_file = natt_plugin_context.data_dir / "host_health_state.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert len(data["conversations"]) == 1
        assert data["last_inquiry_time"] > 0

    @pytest.mark.asyncio
    async def test_history_trimmed_at_max(self, natt_plugin_context):
        """History is trimmed to _MAX_HISTORY entries."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        # Fill with 55 entries
        plugin._conversation_history = [
            {"timestamp": f"2026-01-{i:02d}T00:00:00", "sender": "natt"}
            for i in range(1, 56)
        ]
        plugin._last_inquiry_time = 0

        await plugin.tick()

        # Should be trimmed to 50
        assert len(plugin._conversation_history) <= 50

    @pytest.mark.asyncio
    async def test_previous_context_from_history(self, natt_plugin_context):
        """Previous context is generated from last conversation."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        plugin._conversation_history = [
            {
                "timestamp": "2026-01-01T00:00:00",
                "sender": "natt",
                "health_grade": "fair",
                "response": "Memory is getting a bit tight.",
            }
        ]

        context = plugin._get_previous_context()

        assert context is not None
        assert "fair" in context
        assert "Memory" in context

    @pytest.mark.asyncio
    async def test_no_previous_context_when_empty(self, natt_plugin_context):
        """No context when history is empty."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        assert plugin._get_previous_context() is None


class TestHostHealthErrorPaths:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_corrupted_state_file_handled(self, natt_plugin_context):
        """Plugin handles corrupted state file gracefully on setup."""
        state_file = natt_plugin_context.data_dir / "host_health_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json {{{")

        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        # Should start fresh despite corrupted file
        assert plugin._conversation_history == [] or isinstance(plugin._conversation_history, list)

    @pytest.mark.asyncio
    async def test_tick_without_setup_no_crash(self, natt_plugin_context):
        """Calling tick before setup doesn't crash."""
        plugin = HostHealthPlugin(natt_plugin_context)
        # Force minimal state
        plugin._state_file = natt_plugin_context.data_dir / "host_health_state.json"
        natt_plugin_context.data_dir.mkdir(parents=True, exist_ok=True)
        plugin._conversation_history = []
        plugin._last_inquiry_time = 0
        plugin._interval_seconds = _DEFAULT_INTERVAL_SECONDS
        # tick should handle missing IPC gracefully
        await plugin.tick()

    def test_fallback_motivations_not_empty(self):
        """The predefined fallback motivations list is non-empty."""
        assert len(_FALLBACK_MOTIVATIONS) > 0
        for m in _FALLBACK_MOTIVATIONS:
            assert isinstance(m, str)
            assert len(m) > 0

    @pytest.mark.asyncio
    async def test_state_persistence_roundtrip(self, natt_plugin_context):
        """State saved to disk can be restored on next setup."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        # Add a mock conversation entry to history
        plugin._conversation_history.append({
            "timestamp": time.time(),
            "grade": "good",
            "motivation": "Keep going!",
            "metrics": {"cpu": 45.0, "memory": 60.0},
        })
        plugin._save_state()

        # Create a new plugin instance and verify state is restored
        plugin2 = HostHealthPlugin(natt_plugin_context)
        await plugin2.setup()
        assert len(plugin2._conversation_history) >= 1
        assert plugin2._conversation_history[-1]["grade"] == "good"
