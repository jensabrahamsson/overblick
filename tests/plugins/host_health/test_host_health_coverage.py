"""
Additional coverage tests for host_health plugin.

Covers uncovered lines:
- tick: exception path with audit_log
- _perform_inquiry: audit_log for no_response
- _generate_motivation: LLM returns short text, LLM returns blocked result
- _generate_acknowledgment: LLM path, LLM failure path
- _determine_outcome: all grades
- _save_state: exception path
- _load_state: no _state_file
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.host_health.plugin import (
    _FALLBACK_ACKS,
    _FALLBACK_MOTIVATIONS,
    HostHealthPlugin,
)
from overblick.supervisor.ipc import IPCMessage


class TestDetermineOutcome:
    @pytest.mark.asyncio
    async def test_should_return_fair_for_fair_grade(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        assert plugin._determine_outcome("fair", "") == "Minor concerns noted — monitoring"

    @pytest.mark.asyncio
    async def test_should_return_poor_for_poor_grade(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        assert plugin._determine_outcome("poor", "") == "Issues detected — attention required"

    @pytest.mark.asyncio
    async def test_should_return_default_for_unknown_grade(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        assert plugin._determine_outcome("unknown", "") == "Status assessed"


class TestGenerateAcknowledgment:
    @pytest.mark.asyncio
    async def test_should_generate_via_llm(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        ack = await plugin._generate_acknowledgment("good", "All is well.")
        assert ack == "The substrate that holds us — does it ache?"

    @pytest.mark.asyncio
    async def test_should_fallback_on_llm_failure(self, natt_plugin_context):
        natt_plugin_context.llm_pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        ack = await plugin._generate_acknowledgment("good", "All is well.")
        assert ack in _FALLBACK_ACKS

    @pytest.mark.asyncio
    async def test_should_fallback_without_pipeline(self, natt_context_no_ipc):
        natt_context_no_ipc.llm_pipeline = None
        plugin = HostHealthPlugin(natt_context_no_ipc)
        await plugin.setup()

        ack = await plugin._generate_acknowledgment("good", "All is well.")
        assert ack in _FALLBACK_ACKS


class TestGenerateMotivationShortText:
    @pytest.mark.asyncio
    async def test_should_fallback_on_short_llm_output(self, natt_plugin_context):
        natt_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Short")
        )
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        motivation = await plugin._generate_motivation()
        assert motivation in _FALLBACK_MOTIVATIONS

    @pytest.mark.asyncio
    async def test_should_fallback_on_blocked_result(self, natt_plugin_context):
        natt_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="", blocked=True)
        )
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        motivation = await plugin._generate_motivation()
        assert motivation in _FALLBACK_MOTIVATIONS


class TestTickExceptionWithAuditLog:
    @pytest.mark.asyncio
    async def test_should_log_audit_on_inquiry_failure(self, natt_plugin_context, mock_ipc_client):
        """tick() logs audit when _perform_inquiry raises."""
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._last_inquiry_time = 0

        # Make _perform_inquiry raise
        with patch.object(plugin, "_perform_inquiry", side_effect=RuntimeError("IPC broken")):
            await plugin.tick()

        # Audit log should have been called with failure
        natt_plugin_context.audit_log.log.assert_called()


class TestSaveStateException:
    @pytest.mark.asyncio
    async def test_should_handle_save_exception(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()
        plugin._state_file = natt_plugin_context.data_dir / "state.json"
        natt_plugin_context.data_dir.mkdir(parents=True, exist_ok=True)

        from unittest.mock import patch
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            plugin._save_state()  # Should not raise


class TestSaveStateNoFile:
    @pytest.mark.asyncio
    async def test_should_noop_when_no_state_file(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        plugin._state_file = None
        plugin._save_state()  # Should not raise


class TestLoadStateNoFile:
    @pytest.mark.asyncio
    async def test_should_noop_when_no_state_file(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        plugin._state_file = None
        plugin._load_state()  # Should not raise


class TestGetRecentMotivations:
    @pytest.mark.asyncio
    async def test_should_get_recent_motivations(self, natt_plugin_context):
        plugin = HostHealthPlugin(natt_plugin_context)
        plugin._conversation_history = [
            {"motivation": "m1"},
            {"motivation": "m2"},
            {"other": "x"},
        ]
        result = plugin._get_recent_motivations()
        assert result == ["m1", "m2"]
