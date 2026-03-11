"""
Tests for ResponseGenerator — LLM-powered engagement responses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.engagement.response_gen import ResponseGenerator
from overblick.core.llm.pipeline import PipelineResult, PipelineStage


def make_pipeline(response="Test response", blocked=False, block_reason=None):
    """Create a mock SafeLLMPipeline."""
    pipeline = AsyncMock()
    result = PipelineResult(
        content=response if not blocked else None,
        blocked=blocked,
        block_reason=block_reason,
        block_stage=PipelineStage.PREFLIGHT if blocked else None,
    )
    pipeline._chat_with_overrides = AsyncMock(return_value=result)
    return pipeline


class TestResponseGenerator:
    def test_initialization_with_pipeline(self):
        pipeline = make_pipeline()
        gen = ResponseGenerator(
            llm_pipeline=pipeline,
        )
        assert gen._pipeline == pipeline

    def test_initialization_with_client_raises_error(self):
        """llm_client parameter is no longer accepted."""
        pipeline = make_pipeline()
        with pytest.raises(TypeError):
            ResponseGenerator(
                llm_pipeline=pipeline,
                llm_client=AsyncMock(),
                system_prompt="You are a test bot.",
                allow_raw_fallback=True,
            )

    def test_initialization_no_llm_raises_error(self):
        """llm_pipeline is required."""
        with pytest.raises(TypeError):
            ResponseGenerator()  # Missing required argument

    @pytest.mark.asyncio
    async def test_generate_basic(self):
        """Basic generate call passes parameters to pipeline."""
        pipeline = make_pipeline("Generated response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate(
            prompt="Test prompt",
            system_prompt="You are a test assistant.",
            user_id="test_user",
            temperature=0.8,
            max_tokens=200,
            audit_action="test_action",
            priority="high",
            complexity="medium",
            skip_preflight=True,
        )

        assert result == "Generated response"
        pipeline._chat_with_overrides.assert_called_once()
        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        assert call_kwargs["user_id"] == "test_user"
        assert call_kwargs["temperature"] == 0.8
        assert call_kwargs["max_tokens"] == 200
        assert call_kwargs["audit_action"] == "test_action"
        assert call_kwargs["priority"] == "high"
        assert call_kwargs["complexity"] == "medium"
        assert call_kwargs["skip_preflight"] is True

        # Verify messages structure
        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "You are a test assistant." in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "<<<EXTERNAL_EXTERNAL_START>>>" in messages[1]["content"]
        assert "Test prompt" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_generate_with_context_items(self):
        """Context items are injected into system prompt."""
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate(
            prompt="Prompt",
            system_prompt="System",
            context_items=["Learning 1", "Learning 2"],
        )

        assert result == "Response"
        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        system_content = call_kwargs["messages"][0]["content"]
        assert "Learning 1" in system_content
        assert "Learning 2" in system_content
        assert "Context and learnings:" in system_content

    @pytest.mark.asyncio
    async def test_generate_blocked_returns_deflection(self):
        """When pipeline blocks, returns deflection text."""
        pipeline = make_pipeline(blocked=True, block_reason="toxic")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate(
            prompt="Bad prompt",
            system_prompt="System",
        )

        # Should return deflection or default message
        assert "I'm not able to" in result or result == ""

    @pytest.mark.asyncio
    async def test_generate_default_parameters(self):
        """Default parameters are passed correctly."""
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        await gen.generate(
            prompt="Test",
            system_prompt="System",
        )

        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        assert call_kwargs["user_id"] == "system"
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 500
        assert call_kwargs["audit_action"] == "generate_response"
        assert call_kwargs["priority"] == "low"
        assert call_kwargs["complexity"] is None
        assert call_kwargs["skip_preflight"] is False

    @pytest.mark.asyncio
    async def test_generate_empty_context_items(self):
        """Empty context_items list does not modify system prompt."""
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        await gen.generate(
            prompt="Test",
            system_prompt="Original system prompt",
            context_items=[],
        )

        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        system_content = call_kwargs["messages"][0]["content"]
        assert system_content == "Original system prompt"
        assert "Context and learnings:" not in system_content

    @pytest.mark.asyncio
    async def test_generate_none_context_items(self):
        """None context_items is handled."""
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        await gen.generate(
            prompt="Test",
            system_prompt="Original",
            context_items=None,
        )

        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        system_content = call_kwargs["messages"][0]["content"]
        assert system_content == "Original"
