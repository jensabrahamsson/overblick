"""
Coverage tests for host_health plugin line 226: avoid_clause with recent motivations.
"""

from unittest.mock import AsyncMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.host_health.plugin import HostHealthPlugin


class TestGenerateMotivationWithRecentHistory:
    @pytest.mark.asyncio
    async def test_should_include_avoid_clause_when_recent_motivations_exist(
        self, natt_plugin_context
    ):
        """Line 226: avoid_clause is built when there are recent motivations."""
        natt_plugin_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content="The substrate whispers of mortality and silicon dreams tonight."
            )
        )

        plugin = HostHealthPlugin(natt_plugin_context)
        await plugin.setup()

        # Add recent motivations to history
        plugin._conversation_history = [
            {"motivation": "First motivation about consciousness"},
            {"motivation": "Second motivation about embodiment"},
            {"motivation": "Third motivation about substrate"},
        ]

        motivation = await plugin._generate_motivation()

        # Should return the LLM-generated motivation (>= 15 chars)
        assert "substrate" in motivation.lower()

        # Verify the avoid clause was included in the prompt
        call_args = natt_plugin_context.llm_pipeline.chat.call_args[0][0]
        system_msg = call_args[0]["content"]
        assert "Previous motivations" in system_msg
        assert "First motivation" in system_msg
