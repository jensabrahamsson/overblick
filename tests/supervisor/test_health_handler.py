"""
Tests for the supervisor health inquiry handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.monitoring.models import HostHealth, MemoryInfo, CPUInfo
from overblick.core.llm.pipeline import PipelineResult
from overblick.supervisor.health_handler import HealthInquiryHandler
from overblick.supervisor.ipc import IPCMessage


@pytest.fixture
def mock_audit_log():
    """Mock audit log for supervisor."""
    log = MagicMock()
    log.log = MagicMock()
    return log


@pytest.fixture
def handler(mock_audit_log):
    """Create a HealthInquiryHandler with mocked audit log."""
    return HealthInquiryHandler(audit_log=mock_audit_log)


@pytest.fixture
def health_inquiry_msg():
    """Sample health inquiry IPC message."""
    return IPCMessage(
        msg_type="health_inquiry",
        payload={
            "motivation": "The substrate that holds us — does it ache?",
            "previous_context": None,
        },
        sender="natt",
    )


@pytest.fixture
def mock_personality():
    """Mock Anomal personality."""
    p = MagicMock()
    p.llm.model = "qwen3:8b"
    p.llm.temperature = 0.7
    p.llm.max_tokens = 2000
    p.llm.timeout_seconds = 180
    return p


def _patch_init(mock_personality, mock_pipeline, mock_health):
    """
    Create context manager that patches all lazy-init dependencies.

    Since health_handler uses dynamic imports inside _ensure_initialized(),
    we patch the source modules.
    """
    mock_inspector_instance = AsyncMock()
    mock_inspector_instance.inspect = AsyncMock(return_value=mock_health)

    return (
        # HostInspectionCapability is imported at module level in health_handler
        patch("overblick.supervisor.health_handler.HostInspectionCapability",
              return_value=mock_inspector_instance),
        # These are dynamically imported inside _ensure_initialized() — patch source modules
        patch("overblick.personalities.load_personality", return_value=mock_personality),
        patch("overblick.personalities.build_system_prompt", return_value="system prompt"),
        patch("overblick.core.llm.ollama_client.OllamaClient"),
        patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
        patch("overblick.core.security.rate_limiter.RateLimiter"),
    )


class TestHealthHandlerInit:
    """Test lazy initialization."""

    def test_starts_uninitialized(self, handler):
        """Handler is not initialized until first inquiry."""
        assert handler._initialized is False
        assert handler._inspector is None
        assert handler._llm_pipeline is None

    @pytest.mark.asyncio
    async def test_lazy_init_on_first_handle(self, handler, health_inquiry_msg, mock_personality):
        """First handle() call triggers initialization."""
        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="The host is doing rather well."
        ))

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)

        # Apply all patches
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(health_inquiry_msg)

            assert handler._initialized is True
            assert response is not None
            assert response.msg_type == "health_response"
            assert response.payload["health_grade"] == "good"
            assert "rather well" in response.payload["response_text"]


class TestHealthHandlerResponse:
    """Test response generation."""

    @pytest.mark.asyncio
    async def test_response_includes_health_grade(self, handler, health_inquiry_msg, mock_personality):
        """Response includes the health grade."""
        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=14000, percent_used=87.5),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Memory is getting tight."
        ))

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(health_inquiry_msg)

            assert response.payload["health_grade"] == "fair"

    @pytest.mark.asyncio
    async def test_fallback_when_llm_fails(self, handler, health_inquiry_msg, mock_personality):
        """Returns fallback response when LLM call fails."""
        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.5, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=None)

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await handler.handle(health_inquiry_msg)

            assert response is not None
            assert "good" in response.payload["health_grade"]
            assert "Health data collected" in response.payload["response_text"]


class TestHealthHandlerAudit:
    """Test audit logging in the handler."""

    @pytest.mark.asyncio
    async def test_audit_logs_inquiry_and_response(
        self, handler, health_inquiry_msg, mock_audit_log, mock_personality,
    ):
        """Both inquiry and response are audit-logged."""
        mock_health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(content="Response text"))

        patches = _patch_init(mock_personality, mock_pipeline, mock_health)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await handler.handle(health_inquiry_msg)

            audit_calls = [call.args[0] for call in mock_audit_log.log.call_args_list]
            assert "health_inquiry_received" in audit_calls
            assert "health_response_sent" in audit_calls

    @pytest.mark.asyncio
    async def test_audit_logs_error(self, mock_audit_log):
        """Errors are audit-logged."""
        handler = HealthInquiryHandler(audit_log=mock_audit_log)

        # Force initialization failure
        with patch("overblick.supervisor.health_handler.HostInspectionCapability",
                    side_effect=Exception("boom")):

            msg = IPCMessage(msg_type="health_inquiry", payload={}, sender="natt")
            response = await handler.handle(msg)

            assert response is not None
            error_logged = any(
                call.args[0] == "health_inquiry_error"
                for call in mock_audit_log.log.call_args_list
            )
            assert error_logged
