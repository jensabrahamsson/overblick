"""
Tests for SummarizerCapability — LLM-powered text summarization.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from blick.core.capability import CapabilityContext
from blick.core.llm.pipeline import PipelineResult
from blick.capabilities.content.summarizer import SummarizerCapability


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestSummarizerCapability:
    @pytest.mark.asyncio
    async def test_setup(self):
        ctx = make_ctx(config={"temperature": 0.5, "max_tokens": 300})
        cap = SummarizerCapability(ctx)
        await cap.setup()
        assert cap._temperature == 0.5
        assert cap._max_tokens == 300

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = SummarizerCapability(ctx)
        assert cap.name == "summarizer"

    @pytest.mark.asyncio
    async def test_summarize_with_pipeline(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="This is a summary of the text.",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("A long text about many topics.", max_length=50)
        assert result == "This is a summary of the text."
        pipeline.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_with_client_fallback(self):
        client = AsyncMock()
        client.chat = AsyncMock(return_value={"content": "Summary via client."})
        ctx = make_ctx(llm_client=client)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("A long text about many topics.")
        assert result == "Summary via client."

    @pytest.mark.asyncio
    async def test_summarize_empty_text(self):
        ctx = make_ctx()
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_summarize_whitespace_only(self):
        ctx = make_ctx()
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("   ")
        assert result == ""

    @pytest.mark.asyncio
    async def test_summarize_no_llm(self):
        ctx = make_ctx()
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text that needs summarizing.")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_pipeline_blocked(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=PipelineResult(
            blocked=True, block_reason="Safety check failed",
        ))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text.")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_pipeline_error(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(side_effect=Exception("LLM down"))
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = SummarizerCapability(ctx)
        await cap.setup()

        result = await cap.summarize("Some text.")
        assert result is None
