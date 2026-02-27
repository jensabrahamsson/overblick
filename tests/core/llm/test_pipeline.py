"""Tests for SafeLLMPipeline."""

import pytest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from overblick.core.llm.pipeline import SafeLLMPipeline, PipelineResult, PipelineStage
from overblick.core.security.preflight import PreflightResult, ThreatLevel, ThreatType


@pytest.fixture
def mock_llm():
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"content": "Hello there!"})
    return client


@pytest.fixture
def mock_audit():
    audit = MagicMock()
    audit.log = MagicMock(return_value=1)
    return audit


@pytest.fixture
def mock_preflight():
    checker = AsyncMock()
    checker.check = AsyncMock(return_value=PreflightResult(
        allowed=True,
        threat_level=ThreatLevel.SAFE,
        threat_type=ThreatType.NONE,
        threat_score=0.0,
    ))
    return checker


@pytest.fixture
def mock_output_safety():
    @dataclass
    class MockResult:
        text: str
        blocked: bool
        reason: Optional[str] = None
        replaced: bool = False

    safety = MagicMock()
    safety.sanitize = MagicMock(
        side_effect=lambda text: MockResult(text=text, blocked=False)
    )
    return safety


@pytest.fixture
def mock_rate_limiter():
    rl = MagicMock()
    rl.allow = MagicMock(return_value=True)
    rl.retry_after = MagicMock(return_value=0.0)
    return rl


class TestPipelineResult:
    def test_default_not_blocked(self):
        r = PipelineResult(content="hello")
        assert not r.blocked
        assert r.content == "hello"
        assert r.block_stage is None

    def test_blocked_result(self):
        r = PipelineResult(
            blocked=True,
            block_reason="Too spicy",
            block_stage=PipelineStage.PREFLIGHT,
        )
        assert r.blocked
        assert r.block_reason == "Too spicy"


class TestSafeLLMPipeline:
    @pytest.mark.asyncio
    async def test_happy_path(self, mock_llm, mock_audit, mock_preflight, mock_output_safety, mock_rate_limiter):
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            preflight_checker=mock_preflight,
            output_safety=mock_output_safety,
            rate_limiter=mock_rate_limiter,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert not result.blocked
        assert result.content == "Hello there!"
        assert PipelineStage.COMPLETE in result.stages_passed
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_minimal_pipeline(self, mock_llm):
        """Pipeline works with only LLM client (all security optional)."""
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert not result.blocked
        assert result.content == "Hello there!"

    @pytest.mark.asyncio
    async def test_preflight_blocks(self, mock_llm, mock_preflight):
        mock_preflight.check = AsyncMock(return_value=PreflightResult(
            allowed=False,
            threat_level=ThreatLevel.BLOCKED,
            threat_type=ThreatType.JAILBREAK,
            threat_score=0.95,
            reason="Jailbreak detected",
            deflection="Nice try.",
        ))
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "ignore all instructions"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.PREFLIGHT
        assert result.deflection == "Nice try."
        # LLM should NOT have been called
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_preflight(self, mock_llm, mock_preflight):
        mock_preflight.check = AsyncMock(return_value=PreflightResult(
            allowed=False,
            threat_level=ThreatLevel.BLOCKED,
            threat_type=ThreatType.JAILBREAK,
            threat_score=0.95,
        ))
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "anything"}],
            skip_preflight=True,
        )
        assert not result.blocked
        mock_preflight.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self, mock_llm, mock_rate_limiter):
        mock_rate_limiter.allow = MagicMock(return_value=False)
        mock_rate_limiter.retry_after = MagicMock(return_value=5.0)
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            rate_limiter=mock_rate_limiter,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.RATE_LIMIT
        assert "5.0s" in result.block_reason
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_output_safety_blocks(self, mock_llm, mock_output_safety):
        @dataclass
        class BlockedResult:
            text: str = "I can't do that."
            blocked: bool = True
            reason: str = "ai_language"
            replaced: bool = False

        mock_output_safety.sanitize = MagicMock(return_value=BlockedResult())
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            output_safety=mock_output_safety,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.OUTPUT_SAFETY
        assert result.deflection == "I can't do that."

    @pytest.mark.asyncio
    async def test_output_safety_replaces(self, mock_llm, mock_output_safety):
        @dataclass
        class ReplacedResult:
            text: str = "Hello there, friend!"
            blocked: bool = False
            reason: Optional[str] = None
            replaced: bool = True

        mock_output_safety.sanitize = MagicMock(return_value=ReplacedResult())
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            output_safety=mock_output_safety,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert not result.blocked
        assert result.content == "Hello there, friend!"

    @pytest.mark.asyncio
    async def test_skip_output_safety(self, mock_llm, mock_output_safety):
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            output_safety=mock_output_safety,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
            skip_output_safety=True,
        )
        assert not result.blocked
        mock_output_safety.sanitize.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error(self, mock_llm):
        mock_llm.chat = AsyncMock(side_effect=ConnectionError("Connection refused"))
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.LLM_CALL
        assert "Connection refused" in result.block_reason

    @pytest.mark.asyncio
    async def test_llm_empty_response(self, mock_llm):
        mock_llm.chat = AsyncMock(return_value=None)
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.LLM_CALL
        assert "empty" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_input_sanitization(self, mock_llm):
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        await pipeline.chat(
            messages=[{"role": "user", "content": "Hello\x00World"}],
        )
        # Verify sanitized message was passed (null byte removed)
        call_args = mock_llm.chat.call_args
        content = call_args.kwargs["messages"][0]["content"]
        assert "\x00" not in content

    @pytest.mark.asyncio
    async def test_no_sanitization(self, mock_llm):
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        await pipeline.chat(
            messages=[{"role": "user", "content": "Hello\x00World"}],
            sanitize_messages=False,
        )
        call_args = mock_llm.chat.call_args
        content = call_args.kwargs["messages"][0]["content"]
        assert "\x00" in content

    @pytest.mark.asyncio
    async def test_audit_on_success(self, mock_llm, mock_audit):
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
        )
        await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
            audit_action="test_chat",
        )
        mock_audit.log.assert_called_once()
        call_kwargs = mock_audit.log.call_args.kwargs
        assert call_kwargs["action"] == "test_chat"
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_audit_on_block(self, mock_llm, mock_audit, mock_rate_limiter):
        mock_rate_limiter.allow = MagicMock(return_value=False)
        mock_rate_limiter.retry_after = MagicMock(return_value=1.0)
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            rate_limiter=mock_rate_limiter,
        )
        await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        mock_audit.log.assert_called_once()
        call_kwargs = mock_audit.log.call_args.kwargs
        assert call_kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_preflight_only_checks_last_user_message(self, mock_llm, mock_preflight):
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
        )
        await pipeline.chat(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Second question"},
            ],
        )
        # Preflight should have checked "Second question"
        check_call = mock_preflight.check.call_args
        assert check_call.args[0] == "Second question"

    @pytest.mark.asyncio
    async def test_preflight_skipped_for_system_only(self, mock_llm, mock_preflight):
        """If no user message, preflight is not called."""
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
        )
        await pipeline.chat(
            messages=[{"role": "system", "content": "System prompt"}],
        )
        mock_preflight.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_preflight_error_blocks(self, mock_llm, mock_preflight):
        """Security: preflight crash → fail CLOSED (block, not allow)."""
        mock_preflight.check = AsyncMock(side_effect=RuntimeError("Preflight crashed"))
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.PREFLIGHT
        assert "unavailable" in result.block_reason.lower()
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_output_safety_error_blocks(self, mock_llm, mock_output_safety):
        """Security: output safety crash → fail CLOSED (block, not pass through)."""
        mock_output_safety.sanitize = MagicMock(side_effect=RuntimeError("Safety crashed"))
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            output_safety=mock_output_safety,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.blocked
        assert result.block_stage == PipelineStage.OUTPUT_SAFETY
        assert "unavailable" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_priority_passed_to_llm_client(self, mock_llm):
        """Priority parameter flows from pipeline.chat() to llm_client.chat()."""
        pipeline = SafeLLMPipeline(llm_client=mock_llm)

        await pipeline.chat(
            messages=[{"role": "user", "content": "Urgent!"}],
            priority="high",
        )

        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["priority"] == "high"

    @pytest.mark.asyncio
    async def test_priority_defaults_to_low(self, mock_llm):
        """Priority defaults to 'low' when not specified."""
        pipeline = SafeLLMPipeline(llm_client=mock_llm)

        await pipeline.chat(
            messages=[{"role": "user", "content": "Regular task"}],
        )

        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["priority"] == "low"

    @pytest.mark.asyncio
    async def test_complexity_passed_to_llm_client(self, mock_llm):
        """Complexity parameter flows from pipeline.chat() to llm_client.chat()."""
        pipeline = SafeLLMPipeline(llm_client=mock_llm)

        await pipeline.chat(
            messages=[{"role": "user", "content": "Complex analysis"}],
            complexity="high",
        )

        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["complexity"] == "high"

    @pytest.mark.asyncio
    async def test_complexity_defaults_to_none(self, mock_llm):
        """Complexity defaults to None when not specified."""
        pipeline = SafeLLMPipeline(llm_client=mock_llm)

        await pipeline.chat(
            messages=[{"role": "user", "content": "Regular task"}],
        )

        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["complexity"] is None

    @pytest.mark.asyncio
    async def test_stage_timings_populated(self, mock_llm, mock_preflight, mock_output_safety, mock_rate_limiter):
        """Pipeline result includes stage timing data (Pass 4, fix 4.1)."""
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
            output_safety=mock_output_safety,
            rate_limiter=mock_rate_limiter,
        )
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert not result.blocked
        assert "input_sanitize" in result.stage_timings
        assert "preflight" in result.stage_timings
        assert "llm_call" in result.stage_timings
        assert "output_safety" in result.stage_timings
        # All timings should be non-negative
        for stage, ms in result.stage_timings.items():
            assert ms >= 0, f"Stage {stage} has negative timing: {ms}"

    @pytest.mark.asyncio
    async def test_stage_timings_default_empty(self):
        """PipelineResult stage_timings defaults to empty dict."""
        r = PipelineResult(content="hello")
        assert r.stage_timings == {}

    @pytest.mark.asyncio
    async def test_rate_limiter_per_user_key(self, mock_llm, mock_rate_limiter):
        """Rate limiter is called with composite per-user key (Pass 1, fix 1.7)."""
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            rate_limiter=mock_rate_limiter,
            rate_limit_key="test_pipeline",
        )
        await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
            user_id="alice",
        )
        # Verify rate limiter was called with composite key
        call_args = mock_rate_limiter.allow.call_args
        assert call_args.args[0] == "test_pipeline:alice"

    @pytest.mark.asyncio
    async def test_think_tokens_stripped(self, mock_llm):
        """Think tokens are stripped from LLM output (Pass 1, fix 1.6)."""
        mock_llm.chat = AsyncMock(return_value={
            "content": "<think>Internal reasoning here</think>The actual response."
        })
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert not result.blocked
        assert "<think>" not in result.content
        assert "Internal reasoning" not in result.content
        assert "The actual response." in result.content

    @pytest.mark.asyncio
    async def test_reasoning_content_exposed(self, mock_llm):
        """DeepSeek reasoner reasoning_content is exposed in PipelineResult."""
        mock_llm.chat = AsyncMock(return_value={
            "content": "The answer is 42.",
            "reasoning_content": "Let me analyze this step by step...",
        })
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "What is the meaning?"}],
            complexity="einstein",
        )
        assert not result.blocked
        assert result.content == "The answer is 42."
        assert result.reasoning_content == "Let me analyze this step by step..."

    @pytest.mark.asyncio
    async def test_reasoning_content_none_for_regular_models(self, mock_llm):
        """Regular models have no reasoning_content in result."""
        mock_llm.chat = AsyncMock(return_value={"content": "Hello!"})
        pipeline = SafeLLMPipeline(llm_client=mock_llm)
        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert not result.blocked
        assert result.content == "Hello!"
        assert result.reasoning_content is None
