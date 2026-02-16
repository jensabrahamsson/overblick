"""Fail-closed security tests for SafeLLMPipeline.

Validates that the pipeline blocks on ANY exception or failure in
security-critical stages. This is the security contract: when in
doubt, deny.
"""

import logging
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from overblick.core.exceptions import ConfigError
from overblick.core.llm.pipeline import (
    PipelineResult,
    PipelineStage,
    SafeLLMPipeline,
)
from overblick.core.security.preflight import (
    PreflightResult,
    ThreatLevel,
    ThreatType,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """LLM client that returns a valid response by default."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"content": "All good."})
    return client


@pytest.fixture
def mock_audit():
    """Audit log that records calls for assertion."""
    audit = MagicMock()
    audit.log = MagicMock(return_value=1)
    return audit


@pytest.fixture
def mock_preflight():
    """Preflight checker that allows everything by default."""
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
    """Output safety filter that passes content through unchanged."""
    @dataclass
    class SafeResult:
        text: str
        blocked: bool
        reason: Optional[str] = None
        replaced: bool = False

    safety = MagicMock()
    safety.sanitize = MagicMock(
        side_effect=lambda text: SafeResult(text=text, blocked=False)
    )
    return safety


@pytest.fixture
def mock_rate_limiter():
    """Rate limiter that allows all requests by default."""
    rl = MagicMock()
    rl.allow = MagicMock(return_value=True)
    rl.retry_after = MagicMock(return_value=0.0)
    return rl


@pytest.fixture
def full_pipeline(mock_llm, mock_audit, mock_preflight, mock_output_safety, mock_rate_limiter):
    """Pipeline with all components wired up."""
    return SafeLLMPipeline(
        llm_client=mock_llm,
        audit_log=mock_audit,
        preflight_checker=mock_preflight,
        output_safety=mock_output_safety,
        rate_limiter=mock_rate_limiter,
        identity_name="test-agent",
    )


# ── Test class ──────────────────────────────────────────────────────────────


class TestPipelineFailClosed:
    """
    Verify fail-closed behaviour: any exception in a security-critical
    stage MUST produce blocked=True, never silently pass through.
    """

    @pytest.mark.asyncio
    async def test_preflight_exception_blocks(self, mock_llm, mock_audit, mock_rate_limiter):
        """When preflight.check() raises an exception, pipeline returns blocked=True."""
        preflight = AsyncMock()
        preflight.check = AsyncMock(
            side_effect=RuntimeError("Preflight service unavailable")
        )

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            preflight_checker=preflight,
            rate_limiter=mock_rate_limiter,
        )

        result = await pipeline.chat(
            messages=[{"role": "user", "content": "test message"}],
        )

        assert result.blocked is True
        assert result.block_stage == PipelineStage.PREFLIGHT
        assert "unavailable" in result.block_reason.lower()
        # LLM must never be called when preflight fails closed
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_output_safety_exception_blocks(self, mock_llm, mock_audit, mock_rate_limiter):
        """When output_safety.sanitize() raises an exception, pipeline returns blocked=True."""
        output_safety = MagicMock()
        output_safety.sanitize = MagicMock(
            side_effect=ValueError("Safety model corrupted")
        )

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            output_safety=output_safety,
            rate_limiter=mock_rate_limiter,
        )

        result = await pipeline.chat(
            messages=[{"role": "user", "content": "test message"}],
        )

        assert result.blocked is True
        assert result.block_stage == PipelineStage.OUTPUT_SAFETY
        assert "unavailable" in result.block_reason.lower()
        # LLM WAS called (output safety runs after), but content must not leak
        assert result.content is None

    @pytest.mark.asyncio
    async def test_llm_call_exception_blocks(self, mock_llm, mock_audit, mock_rate_limiter):
        """When llm.chat() raises an exception, pipeline returns blocked=True."""
        mock_llm.chat = AsyncMock(
            side_effect=ConnectionError("Ollama connection refused")
        )

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            rate_limiter=mock_rate_limiter,
        )

        result = await pipeline.chat(
            messages=[{"role": "user", "content": "test message"}],
        )

        assert result.blocked is True
        assert result.block_stage == PipelineStage.LLM_CALL
        assert "Ollama connection refused" in result.block_reason

    @pytest.mark.asyncio
    async def test_all_stages_pass(
        self, mock_llm, mock_audit, mock_preflight, mock_output_safety, mock_rate_limiter
    ):
        """On success, all 6 pipeline stages appear in stages_passed."""
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
        expected_stages = [
            PipelineStage.INPUT_SANITIZE,
            PipelineStage.PREFLIGHT,
            PipelineStage.RATE_LIMIT,
            PipelineStage.LLM_CALL,
            PipelineStage.OUTPUT_SAFETY,
            PipelineStage.COMPLETE,
        ]
        assert result.stages_passed == expected_stages

    @pytest.mark.asyncio
    async def test_empty_llm_response_blocks(self, mock_llm, mock_audit, mock_rate_limiter):
        """LLM returning None or empty dict results in blocked=True."""
        for empty_value in [None, {}, ""]:
            mock_llm.chat = AsyncMock(return_value=empty_value)

            pipeline = SafeLLMPipeline(
                llm_client=mock_llm,
                audit_log=mock_audit,
                rate_limiter=mock_rate_limiter,
            )

            result = await pipeline.chat(
                messages=[{"role": "user", "content": "Hello"}],
            )

            assert result.blocked is True, (
                f"Expected blocked=True for empty LLM response: {empty_value!r}"
            )
            assert result.block_stage == PipelineStage.LLM_CALL
            assert "empty" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self, mock_llm, mock_audit):
        """Rate limiter returning False blocks with retry information."""
        rate_limiter = MagicMock()
        rate_limiter.allow = MagicMock(return_value=False)
        rate_limiter.retry_after = MagicMock(return_value=12.5)

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            rate_limiter=rate_limiter,
        )

        result = await pipeline.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert result.blocked is True
        assert result.block_stage == PipelineStage.RATE_LIMIT
        assert "12.5" in result.block_reason
        assert "retry" in result.block_reason.lower()
        # LLM must not be called when rate limited
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_result_is_audited(self, mock_llm, mock_audit):
        """Verify audit_log.log() is called with success=False when pipeline blocks."""
        preflight = AsyncMock()
        preflight.check = AsyncMock(
            side_effect=RuntimeError("Crash")
        )

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            preflight_checker=preflight,
        )

        result = await pipeline.chat(
            messages=[{"role": "user", "content": "test"}],
            audit_action="agent_reply",
        )

        assert result.blocked is True
        # Audit must have been called exactly once
        mock_audit.log.assert_called_once()
        audit_kwargs = mock_audit.log.call_args.kwargs
        assert audit_kwargs["success"] is False
        assert audit_kwargs["category"] == "security"
        assert "block_reason" in audit_kwargs["details"]
        assert audit_kwargs["action"] == "agent_reply_blocked"

    @pytest.mark.asyncio
    async def test_missing_security_components_warns(
        self, mock_llm, caplog
    ):
        """Pipeline with None preflight/output_safety/rate_limiter logs warnings but still works."""
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            # All security components intentionally omitted
        )

        with caplog.at_level(logging.WARNING, logger="overblick.core.llm.pipeline"):
            result = await pipeline.chat(
                messages=[{"role": "user", "content": "Hello"}],
            )

        # Pipeline should still produce a valid result
        assert not result.blocked
        assert result.content == "All good."

        # Warnings should be emitted for missing components
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        warned_components = " ".join(warning_messages).lower()
        assert "preflight" in warned_components
        assert "rate_limiter" in warned_components
        assert "output_safety" in warned_components

    def test_strict_mode_raises_on_missing_components(self, mock_llm):
        """strict=True raises ConfigError when security components are missing."""
        with pytest.raises(ConfigError, match="strict mode"):
            SafeLLMPipeline(
                llm_client=mock_llm,
                strict=True,
                # All security components intentionally omitted
            )

    def test_strict_mode_partial_missing(self, mock_llm, mock_preflight):
        """strict=True raises when some but not all components are provided."""
        with pytest.raises(ConfigError, match="output_safety"):
            SafeLLMPipeline(
                llm_client=mock_llm,
                preflight_checker=mock_preflight,
                strict=True,
            )

    def test_strict_mode_all_present(
        self, mock_llm, mock_preflight, mock_output_safety, mock_rate_limiter
    ):
        """strict=True succeeds when all security components are provided."""
        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            preflight_checker=mock_preflight,
            output_safety=mock_output_safety,
            rate_limiter=mock_rate_limiter,
            strict=True,
        )
        assert pipeline._strict is True

    @pytest.mark.asyncio
    async def test_audit_details_not_mutated(self, mock_llm, mock_audit, mock_rate_limiter):
        """audit_details dict passed to chat() should not be mutated."""
        preflight = AsyncMock()
        preflight.check = AsyncMock(
            side_effect=RuntimeError("Crash")
        )

        pipeline = SafeLLMPipeline(
            llm_client=mock_llm,
            audit_log=mock_audit,
            preflight_checker=preflight,
        )

        original_details = {"custom_key": "value"}
        details_copy = dict(original_details)

        await pipeline.chat(
            messages=[{"role": "user", "content": "test"}],
            audit_details=original_details,
        )

        # Original dict should NOT have been mutated
        assert original_details == details_copy
