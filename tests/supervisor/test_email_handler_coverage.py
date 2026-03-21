"""Additional tests for email_handler — cover lines 40, 177, 225-226."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.supervisor.email_handler import EmailConsultationHandler
from overblick.supervisor.ipc import IPCMessage


@pytest.fixture
def mock_audit_log():
    log = MagicMock()
    log.log = MagicMock()
    return log


def _patch_init(mock_personality, mock_pipeline):
    return (
        patch("overblick.identities.load_identity", return_value=mock_personality),
        patch("overblick.identities.build_system_prompt", return_value="system prompt"),
        patch("overblick.core.llm.gateway_client.GatewayClient"),
        patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
        patch("overblick.core.security.rate_limiter.RateLimiter"),
    )


class TestEmailHandlerCoverage:
    @pytest.mark.asyncio
    async def test_ensure_initialized_returns_true_when_already_initialized(self):
        """Cover line 40: _ensure_initialized returns True when already init."""
        handler = EmailConsultationHandler()
        handler._initialized = True
        result = await handler._ensure_initialized()
        assert result is True

    @pytest.mark.asyncio
    async def test_generate_advice_no_pipeline(self):
        """Cover line 177: _generate_advice returns default when no pipeline."""
        handler = EmailConsultationHandler()
        handler._initialized = True
        handler._llm_pipeline = None
        handler._system_prompt = None

        action, reasoning = await handler._generate_advice(
            "question", "from@test.com", "Subject", "notify", 0.5
        )
        assert action == "notify"
        assert "unavailable" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_parse_advice_embedded_json_bad_inner(self, mock_audit_log):
        """Cover lines 225-226: embedded JSON with braces but invalid inner JSON."""
        handler = EmailConsultationHandler(audit_log=mock_audit_log)

        # Non-JSON text with braces that form an extractable but invalid JSON
        action, _reasoning = handler._parse_advice(
            'Some text {not: valid, json: here} more text', "notify"
        )
        # Falls through to text extraction since {not: valid, json: here} is not valid JSON
        assert action == "notify"

    @pytest.mark.asyncio
    async def test_handle_no_audit_log(self):
        """Handler works without audit log."""
        handler = EmailConsultationHandler(audit_log=None)

        mock_personality = MagicMock()
        mock_personality.llm.model = "qwen3:8b"
        mock_personality.llm.temperature = 0.7
        mock_personality.llm.max_tokens = 2000
        mock_personality.llm.timeout_seconds = 180

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                content='{"advised_action": "notify", "reasoning": "Test"}'
            )
        )

        patches = _patch_init(mock_personality, mock_pipeline)
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={"question": "Test"},
            sender="stal",
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(msg)
            assert response is not None
