"""
Tests for the supervisor research handler.

Verifies:
- Lazy initialization
- Web search mocking (DuckDuckGo API)
- LLM summarization
- No-results handling
- Audit logging
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.supervisor.ipc import IPCMessage
from overblick.supervisor.research_handler import ResearchHandler


@pytest.fixture
def mock_audit_log():
    """Mock audit log for supervisor."""
    log = MagicMock()
    log.log = MagicMock()
    return log


@pytest.fixture
def handler(mock_audit_log):
    """Create a ResearchHandler with mocked audit log."""
    return ResearchHandler(audit_log=mock_audit_log)


@pytest.fixture
def research_request_msg():
    """Sample research request IPC message."""
    return IPCMessage(
        msg_type="research_request",
        payload={
            "query": "What is the current EUR/SEK exchange rate?",
            "context": "Financial email reply",
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
    """Create patches for lazy-init dependencies."""
    return (
        patch("overblick.identities.load_identity", return_value=mock_personality),
        patch("overblick.identities.build_system_prompt", return_value="system prompt"),
        patch("overblick.core.llm.ollama_client.OllamaClient"),
        patch("overblick.core.llm.pipeline.SafeLLMPipeline", return_value=mock_pipeline),
        patch("overblick.core.security.rate_limiter.RateLimiter"),
    )


class TestResearchHandlerInit:
    """Test lazy initialization."""

    def test_starts_uninitialized(self, handler):
        """Handler is not initialized until first request."""
        assert handler._initialized is False
        assert handler._llm_pipeline is None

    @pytest.mark.asyncio
    async def test_lazy_init_on_first_handle(
        self, handler, research_request_msg, mock_personality,
    ):
        """First handle() call triggers initialization."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="The EUR/SEK rate is approximately 11.45."
        ))

        ddg_response = {
            "Abstract": "The exchange rate between EUR and SEK is 11.45",
            "AbstractSource": "XE.com",
            "RelatedTopics": [],
        }

        patches = _patch_init(mock_personality, mock_pipeline)

        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patch.object(handler, "_web_search", return_value="EUR/SEK: 11.45"),
        ):
            response = await handler.handle(research_request_msg)

            assert handler._initialized is True
            assert response is not None
            assert response.msg_type == "research_response"
            assert "11.45" in response.payload["summary"]


class TestResearchHandlerSearch:
    """Test web search functionality."""

    @pytest.mark.asyncio
    async def test_extract_ddg_results_with_abstract(self, handler):
        """Extracts abstract from DuckDuckGo response."""
        data = {
            "Abstract": "Python is a programming language.",
            "AbstractSource": "Wikipedia",
            "Answer": "",
            "RelatedTopics": [
                {"Text": "Python 3.13 was released in 2024"},
            ],
            "Infobox": {},
        }
        result = handler._extract_ddg_results(data)

        assert "Python is a programming language" in result
        assert "Wikipedia" in result
        assert "Python 3.13" in result

    @pytest.mark.asyncio
    async def test_extract_ddg_results_with_answer(self, handler):
        """Extracts direct answer from DuckDuckGo response."""
        data = {
            "Abstract": "",
            "Answer": "42",
            "RelatedTopics": [],
            "Infobox": {},
        }
        result = handler._extract_ddg_results(data)
        assert "42" in result

    @pytest.mark.asyncio
    async def test_extract_ddg_results_empty(self, handler):
        """Returns empty string for empty DuckDuckGo response."""
        data = {
            "Abstract": "",
            "Answer": "",
            "RelatedTopics": [],
            "Infobox": {},
        }
        result = handler._extract_ddg_results(data)
        assert result == ""

    @pytest.mark.asyncio
    async def test_extract_ddg_results_with_infobox(self, handler):
        """Extracts infobox data from DuckDuckGo response."""
        data = {
            "Abstract": "",
            "Answer": "",
            "RelatedTopics": [],
            "Infobox": {
                "content": [
                    {"label": "Capital", "value": "Stockholm"},
                    {"label": "Population", "value": "10.4 million"},
                ],
            },
        }
        result = handler._extract_ddg_results(data)
        assert "Stockholm" in result
        assert "10.4 million" in result


class TestResearchHandlerResponse:
    """Test response generation."""

    @pytest.mark.asyncio
    async def test_no_results_returns_message(self, handler, mock_personality):
        """Returns 'no results' message when search finds nothing."""
        msg = IPCMessage(
            msg_type="research_request",
            payload={"query": "xyzzy nonexistent query", "context": ""},
            sender="stal",
        )

        with patch.object(handler, "_web_search", return_value=""):
            response = await handler.handle(msg)

        assert response.msg_type == "research_response"
        assert "No results found" in response.payload["summary"]

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, handler):
        """Returns error for empty research query."""
        msg = IPCMessage(
            msg_type="research_request",
            payload={"query": "", "context": ""},
            sender="stal",
        )

        response = await handler.handle(msg)

        assert response.msg_type == "research_response"
        assert "error" in response.payload

    @pytest.mark.asyncio
    async def test_llm_failure_returns_raw_results(
        self, handler, research_request_msg, mock_personality,
    ):
        """Returns raw search results when LLM summarization fails."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=None)  # LLM failure

        patches = _patch_init(mock_personality, mock_pipeline)

        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patch.object(handler, "_web_search", return_value="Raw search data here"),
        ):
            response = await handler.handle(research_request_msg)

        assert response.msg_type == "research_response"
        # Should contain raw results as fallback
        assert response.payload["summary"]


class TestResearchHandlerAudit:
    """Test audit logging."""

    @pytest.mark.asyncio
    async def test_audit_logs_request_and_response(
        self, handler, research_request_msg, mock_audit_log, mock_personality,
    ):
        """Both request and response are audit-logged."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content="Summary text"
        ))

        patches = _patch_init(mock_personality, mock_pipeline)

        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patch.object(handler, "_web_search", return_value="Search results"),
        ):
            await handler.handle(research_request_msg)

        audit_calls = [call.args[0] for call in mock_audit_log.log.call_args_list]
        assert "research_request_received" in audit_calls
        assert "research_response_sent" in audit_calls

    @pytest.mark.asyncio
    async def test_audit_logs_error(self, mock_audit_log):
        """Errors are audit-logged."""
        handler = ResearchHandler(audit_log=mock_audit_log)

        msg = IPCMessage(
            msg_type="research_request",
            payload={"query": ""},
            sender="stal",
        )

        response = await handler.handle(msg)

        assert response is not None
        error_logged = any(
            call.args[0] == "research_request_error"
            for call in mock_audit_log.log.call_args_list
        )
        assert error_logged
