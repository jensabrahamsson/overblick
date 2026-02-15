"""
Tests for the supervisor email consultation handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.supervisor.email_handler import EmailConsultationHandler
from overblick.supervisor.ipc import IPCMessage


@pytest.fixture
def mock_audit_log():
    """Mock audit log for supervisor."""
    log = MagicMock()
    log.log = MagicMock()
    return log


@pytest.fixture
def handler(mock_audit_log):
    """Create an EmailConsultationHandler with mocked audit log."""
    return EmailConsultationHandler(audit_log=mock_audit_log)


@pytest.fixture
def email_consultation_msg():
    """Sample email consultation IPC message."""
    return IPCMessage(
        msg_type="email_consultation",
        payload={
            "question": "Should I reply to this invoice inquiry?",
            "email_from": "vendor@example.com",
            "email_subject": "Re: Invoice #12345 payment status",
            "tentative_intent": "notify",
            "confidence": 0.65,
        },
        sender="stal",
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


def _patch_init(mock_personality, mock_pipeline):
    """
    Create context manager that patches all lazy-init dependencies.

    Since email_handler uses dynamic imports inside _ensure_initialized(),
    we patch the source modules.
    """
    return (
        patch("overblick.personalities.load_personality", return_value=mock_personality),
        patch("overblick.personalities.build_system_prompt", return_value="system prompt"),
        patch("overblick.core.llm.ollama_client.OllamaClient"),
        patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
        patch("overblick.core.security.rate_limiter.RateLimiter"),
    )


class TestEmailHandlerInit:
    """Test lazy initialization."""

    def test_starts_uninitialized(self, handler):
        """Handler is not initialized until first consultation."""
        assert handler._initialized is False
        assert handler._llm_pipeline is None
        assert handler._system_prompt is None

    @pytest.mark.asyncio
    async def test_lazy_init_on_first_handle(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """First handle() call triggers initialization."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Invoice matters need principal review"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert handler._initialized is True
            assert handler._system_prompt is not None
            assert "EMAIL CONSULTATION ADVISOR" in handler._system_prompt
            assert response is not None
            assert response.msg_type == "email_consultation_response"

    @pytest.mark.asyncio
    async def test_init_failure_returns_fallback(self, handler, email_consultation_msg):
        """If initialization fails, returns fallback response."""
        # Force init failure
        with patch("overblick.personalities.load_personality", side_effect=Exception("boom")):
            response = await handler.handle(email_consultation_msg)

            assert response is not None
            assert response.msg_type == "email_consultation_response"
            assert response.payload["advised_action"] == "notify"
            assert "unavailable" in response.payload["reasoning"]


class TestEmailHandlerResponse:
    """Test response generation."""

    @pytest.mark.asyncio
    async def test_response_includes_advised_action(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Response includes advised action and reasoning."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "reply", "reasoning": "Standard invoice inquiry"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response.payload["advised_action"] == "reply"
            assert "invoice" in response.payload["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_handles_ignore_action(self, handler, mock_personality):
        """Can advise to ignore spam/automated emails."""
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={
                "question": "Is this newsletter important?",
                "email_from": "newsletter@marketing.com",
                "email_subject": "Weekly digest #452",
                "tentative_intent": "ignore",
                "confidence": 0.8,
            },
            sender="stal",
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "ignore", "reasoning": "Automated newsletter"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(msg)

            assert response.payload["advised_action"] == "ignore"

    @pytest.mark.asyncio
    async def test_handles_ask_boss_escalation(self, handler, mock_personality):
        """Can escalate unclear cases to ask_boss."""
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={
                "question": "Unclear legal implications - should I respond?",
                "email_from": "legal@partner.com",
                "email_subject": "Contractual obligations re: clause 7.3",
                "tentative_intent": "notify",
                "confidence": 0.4,
            },
            sender="stal",
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "ask_boss", "reasoning": "Legal complexity requires escalation"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(msg)

            assert response.payload["advised_action"] == "ask_boss"
            assert "escalation" in response.payload["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_fallback_when_llm_fails(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Returns fallback response when LLM call fails."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=None)

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response is not None
            # Should use tentative_intent from the message
            assert response.payload["advised_action"] == "notify"
            assert "failed" in response.payload["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_fallback_when_llm_blocked(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Returns fallback response when LLM output is blocked."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="", blocked=True, block_reason="safety filter"
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response.payload["advised_action"] == "notify"
            assert "failed" in response.payload["reasoning"].lower()


class TestEmailHandlerJSONParsing:
    """Test JSON parsing logic."""

    @pytest.mark.asyncio
    async def test_parses_clean_json(self, handler, email_consultation_msg, mock_personality):
        """Parses clean JSON response."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Important"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response.payload["advised_action"] == "notify"
            assert response.payload["reasoning"] == "Important"

    @pytest.mark.asyncio
    async def test_parses_json_with_markdown_wrapper(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Parses JSON even when wrapped in markdown."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='Here is my advice:\n```json\n{"advised_action": "reply", "reasoning": "Quick response needed"}\n```'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response.payload["advised_action"] == "reply"
            assert "Quick response" in response.payload["reasoning"]

    @pytest.mark.asyncio
    async def test_extracts_action_from_text_when_json_fails(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Extracts action keywords from text when JSON parsing fails."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="I think you should ignore this automated newsletter message."
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response.payload["advised_action"] == "ignore"
            assert "automated newsletter" in response.payload["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_uses_fallback_when_no_action_found(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """Uses tentative_intent when no action can be extracted."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="This is unclear and difficult to categorize."
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            # Should fall back to tentative_intent from message
            assert response.payload["advised_action"] == "notify"


class TestEmailHandlerAudit:
    """Test audit logging in the handler."""

    @pytest.mark.asyncio
    async def test_audit_logs_consultation_received(
        self, handler, email_consultation_msg, mock_audit_log, mock_personality,
    ):
        """Consultation received is audit-logged."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Review needed"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await handler.handle(email_consultation_msg)

            audit_calls = [call.args[0] for call in mock_audit_log.log.call_args_list]
            assert "email_consultation_received" in audit_calls

            # Check that consultation details are logged
            received_call = [
                call for call in mock_audit_log.log.call_args_list
                if call.args[0] == "email_consultation_received"
            ][0]
            details = received_call.kwargs["details"]
            assert details["sender"] == "stal"
            assert details["email_from"] == "vendor@example.com"

    @pytest.mark.asyncio
    async def test_audit_logs_consultation_response(
        self, handler, email_consultation_msg, mock_audit_log, mock_personality,
    ):
        """Consultation response is audit-logged."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "reply", "reasoning": "Standard inquiry response"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await handler.handle(email_consultation_msg)

            audit_calls = [call.args[0] for call in mock_audit_log.log.call_args_list]
            assert "email_consultation_response" in audit_calls

            # Check that response details are logged
            response_call = [
                call for call in mock_audit_log.log.call_args_list
                if call.args[0] == "email_consultation_response"
            ][0]
            details = response_call.kwargs["details"]
            assert details["advised_action"] == "reply"
            assert "inquiry" in details["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_audit_includes_duration(
        self, handler, email_consultation_msg, mock_audit_log, mock_personality,
    ):
        """Response audit log includes duration_ms."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Test"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await handler.handle(email_consultation_msg)

            response_call = [
                call for call in mock_audit_log.log.call_args_list
                if call.args[0] == "email_consultation_response"
            ][0]

            assert "duration_ms" in response_call.kwargs
            assert response_call.kwargs["duration_ms"] >= 0


class TestEmailHandlerEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_handles_missing_optional_fields(self, handler, mock_personality):
        """Handler gracefully handles missing optional fields."""
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={
                "question": "What should I do?",
            },
            sender="stal",
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Safe default"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(msg)

            assert response is not None
            assert response.payload["advised_action"] == "notify"

    @pytest.mark.asyncio
    async def test_handles_no_sender(self, handler, mock_personality):
        """Handler handles messages without sender."""
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={"question": "Test"},
        )

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Test"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(msg)

            assert response is not None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_fallback(
        self, handler, email_consultation_msg, mock_personality,
    ):
        """LLM exceptions are caught and fallback is returned."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=Exception("LLM service down"))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            response = await handler.handle(email_consultation_msg)

            assert response is not None
            assert response.payload["advised_action"] == "notify"
            assert "failed" in response.payload["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_system_prompt_includes_json_format(self, handler, mock_personality):
        """System prompt instructs to respond in JSON format."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Test"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            msg = IPCMessage(
                msg_type="email_consultation",
                payload={"question": "Test"},
                sender="stal",
            )
            await handler.handle(msg)

            assert handler._system_prompt is not None
            assert "JSON" in handler._system_prompt
            assert "advised_action" in handler._system_prompt

    @pytest.mark.asyncio
    async def test_system_prompt_includes_action_types(self, handler, mock_personality):
        """System prompt lists all possible actions."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"advised_action": "notify", "reasoning": "Test"}'
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            msg = IPCMessage(
                msg_type="email_consultation",
                payload={"question": "Test"},
                sender="stal",
            )
            await handler.handle(msg)

            assert "ignore" in handler._system_prompt
            assert "notify" in handler._system_prompt
            assert "reply" in handler._system_prompt
            assert "ask_boss" in handler._system_prompt
