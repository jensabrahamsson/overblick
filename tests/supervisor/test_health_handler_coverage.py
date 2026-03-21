"""Additional tests for health_handler — cover lines 50, 162-164, 218, 228, 251, 257-259."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.monitoring.models import CPUInfo, HostHealth, MemoryInfo
from overblick.core.llm.pipeline import PipelineResult
from overblick.supervisor.health_handler import HealthInquiryHandler
from overblick.supervisor.ipc import IPCMessage


@pytest.fixture
def mock_audit_log():
    log = MagicMock()
    log.log = MagicMock()
    return log


def _patch_init(mock_personality, mock_pipeline, mock_health):
    mock_inspector_instance = AsyncMock()
    mock_inspector_instance.inspect = AsyncMock(return_value=mock_health)

    return (
        patch(
            "overblick.supervisor.health_handler.HostInspectionCapability",
            return_value=mock_inspector_instance,
        ),
        patch("overblick.identities.load_identity", return_value=mock_personality),
        patch("overblick.identities.build_system_prompt", return_value="system prompt"),
        patch("overblick.core.llm.gateway_client.GatewayClient"),
        patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
        patch("overblick.core.security.rate_limiter.RateLimiter"),
    )


class TestHealthHandlerCoverage:
    @pytest.mark.asyncio
    async def test_ensure_initialized_returns_true_when_already_init(self):
        """Cover line 50: _ensure_initialized returns True when already init."""
        handler = HealthInquiryHandler()
        handler._initialized = True
        result = await handler._ensure_initialized()
        assert result is True

    @pytest.mark.asyncio
    async def test_inspector_failure_returns_error(self, mock_audit_log):
        """Cover lines 162-164: inspect() raises exception."""
        mock_personality = MagicMock()
        mock_personality.llm.model = "qwen3:8b"
        mock_personality.llm.temperature = 0.7
        mock_personality.llm.max_tokens = 2000
        mock_personality.llm.timeout_seconds = 180

        mock_pipeline = AsyncMock()

        mock_inspector = AsyncMock()
        mock_inspector.inspect = AsyncMock(side_effect=RuntimeError("Disk on fire"))

        handler = HealthInquiryHandler(audit_log=mock_audit_log)

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={"motivation": "Curious about health"},
            sender="natt",
        )

        with (
            patch(
                "overblick.supervisor.health_handler.HostInspectionCapability",
                return_value=mock_inspector,
            ),
            patch("overblick.identities.load_identity", return_value=mock_personality),
            patch("overblick.identities.build_system_prompt", return_value="prompt"),
            patch("overblick.core.llm.gateway_client.GatewayClient"),
            patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
            patch("overblick.core.security.rate_limiter.RateLimiter"),
        ):
            response = await handler.handle(msg)
            assert "Disk on fire" in response.payload["response_text"]

    @pytest.mark.asyncio
    async def test_generate_response_no_pipeline(self):
        """Cover line 218: _generate_response returns None when no pipeline."""
        handler = HealthInquiryHandler()
        handler._llm_pipeline = None
        handler._system_prompt = None

        from overblick.capabilities.monitoring.models import HealthInquiry

        inquiry = HealthInquiry(sender="natt", motivation="test")
        result = await handler._generate_response(inquiry, "health summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_response_with_previous_context(self, mock_audit_log):
        """Cover line 228: inquiry with previous_context."""
        mock_personality = MagicMock()
        mock_personality.llm.model = "qwen3:8b"
        mock_personality.llm.temperature = 0.7
        mock_personality.llm.max_tokens = 2000
        mock_personality.llm.timeout_seconds = 180

        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="Unique response.")
        )

        handler = HealthInquiryHandler(audit_log=mock_audit_log)

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={
                "motivation": "Wondering about health",
                "previous_context": "Last time you said the CPU was fine.",
            },
            sender="natt",
        )

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(msg)
            assert response.payload["response_text"] == "Unique response."

    @pytest.mark.asyncio
    async def test_generate_response_blocked(self, mock_audit_log):
        """Cover line 251: pipeline blocks the response."""
        mock_personality = MagicMock()
        mock_personality.llm.model = "qwen3:8b"
        mock_personality.llm.temperature = 0.7
        mock_personality.llm.max_tokens = 2000
        mock_personality.llm.timeout_seconds = 180

        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content="", blocked=True, block_reason="safety", block_stage="output_safety"
            )
        )

        handler = HealthInquiryHandler(audit_log=mock_audit_log)

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={"motivation": "Test"},
            sender="natt",
        )

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(msg)
            # Fallback response because pipeline blocked
            assert "Health data collected" in response.payload["response_text"]

    @pytest.mark.asyncio
    async def test_generate_response_llm_exception(self, mock_audit_log):
        """Cover lines 257-259: LLM call raises exception."""
        mock_personality = MagicMock()
        mock_personality.llm.model = "qwen3:8b"
        mock_personality.llm.temperature = 0.7
        mock_personality.llm.max_tokens = 2000
        mock_personality.llm.timeout_seconds = 180

        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=Exception("LLM crashed"))

        handler = HealthInquiryHandler(audit_log=mock_audit_log)

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={"motivation": "Test"},
            sender="natt",
        )

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(msg)
            assert "Health data collected" in response.payload["response_text"]

    @pytest.mark.asyncio
    async def test_error_response_without_audit_log(self):
        """Cover the error_response path without audit log."""
        handler = HealthInquiryHandler(audit_log=None)

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={},
            sender="test",
        )

        # Force init failure
        with patch(
            "overblick.supervisor.health_handler.HostInspectionCapability",
            side_effect=Exception("init failed"),
        ):
            response = await handler.handle(msg)
            assert response is not None
