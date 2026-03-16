"""Additional tests for SummarizerCapability — cover lines 79-81.

Uncovered: 79-81 (pipeline exception path).
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from overblick.capabilities.content.summarizer import SummarizerCapability
from overblick.core.capability import CapabilityContext
from overblick.core.llm.pipeline import PipelineResult


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestSummarizerCoverage:
    @pytest.mark.asyncio
    async def test_summarize_pipeline_exception(self):
        """Cover lines 79-81: exception during _chat_with_overrides."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            side_effect=Exception("LLM pipeline error")
        )
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text that needs summarizing.")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_pipeline_blocked(self):
        """Cover lines 75-77: pipeline blocks the result."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(
                content=None,
                blocked=True,
                block_reason="Safety check failed",
            )
        )
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text.")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_pipeline_null_content(self):
        """Pipeline returns non-blocked result but content is None."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(content=None, blocked=False)
        )
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text.")
        assert result is None
