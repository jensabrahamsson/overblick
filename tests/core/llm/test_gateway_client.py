"""
Tests for the GatewayClient LLM interface.

Verifies:
- Initialization with default and custom parameters
- Priority handling (default, per-request, override)
- Chat response parsing (content, model, tokens, finish_reason)
- Health check (success, failure, exception)
- Error handling (HTTP errors, timeout, connection error, unexpected errors)
- Session lifecycle (lazy creation, close)
- Empty/malformed response handling
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from overblick.core.exceptions import LLMConnectionError, LLMTimeoutError

from overblick.core.llm.gateway_client import GatewayClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_session(
    response_status=200,
    response_json=None,
    response_text="error",
    post_side_effect=None,
    get_side_effect=None,
):
    """Create a mock aiohttp session with configurable responses."""
    session = MagicMock()
    session.closed = False

    if response_json is None:
        response_json = {
            "choices": [{"message": {"content": "Test response"}, "finish_reason": "stop"}],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 42},
        }

    mock_response = AsyncMock()
    mock_response.status = response_status
    mock_response.json = AsyncMock(return_value=response_json)
    mock_response.text = AsyncMock(return_value=response_text)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    if post_side_effect:
        session.post = MagicMock(side_effect=post_side_effect)
    else:
        session.post = MagicMock(return_value=mock_response)

    if get_side_effect:
        session.get = MagicMock(side_effect=get_side_effect)
    else:
        # Default GET response for health check
        health_response = AsyncMock()
        health_response.status = 200
        health_response.json = AsyncMock(return_value={"status": "ok", "model": "qwen3:8b"})
        health_response.__aenter__ = AsyncMock(return_value=health_response)
        health_response.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=health_response)

    session.close = AsyncMock()

    return session


def _make_client(session=None, **kwargs):
    """Create a GatewayClient with optional pre-injected session."""
    defaults = {
        "base_url": "http://127.0.0.1:8200",
        "model": "qwen3:8b",
        "default_priority": "low",
    }
    defaults.update(kwargs)
    client = GatewayClient(**defaults)
    if session is not None:
        client._session = session
    return client


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestGatewayClientInit:
    """Test GatewayClient initialization."""

    def test_default_parameters(self):
        """Client initializes with sensible defaults."""
        client = GatewayClient()
        assert client.base_url == "http://127.0.0.1:8200"
        assert client.model == "qwen3:8b"
        assert client.default_priority == "low"
        assert client.max_tokens == 2000
        assert client.temperature == 0.7
        assert client.top_p == 0.9
        assert client.timeout_seconds == 300
        assert client._session is None

    def test_custom_parameters(self):
        """Client accepts custom parameters."""
        client = GatewayClient(
            base_url="http://localhost:9999/",
            model="llama3:70b",
            default_priority="high",
            max_tokens=4096,
            temperature=0.3,
            top_p=0.95,
            timeout_seconds=120,
        )
        assert client.base_url == "http://localhost:9999"  # Trailing slash stripped
        assert client.model == "llama3:70b"
        assert client.default_priority == "high"
        assert client.max_tokens == 4096
        assert client.temperature == 0.3
        assert client.top_p == 0.95
        assert client.timeout_seconds == 120

    def test_base_url_trailing_slash_stripped(self):
        """Trailing slash is removed from base_url."""
        client = GatewayClient(base_url="http://example.com:8200/")
        assert client.base_url == "http://example.com:8200"

    def test_session_starts_none(self):
        """Session is not created at init time."""
        client = GatewayClient()
        assert client._session is None


# ---------------------------------------------------------------------------
# Priority handling
# ---------------------------------------------------------------------------

class TestGatewayClientPriority:
    """Tests for GatewayClient priority handling."""

    async def test_per_request_high_priority(self):
        """Priority can be set per-request, overriding default."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Urgent!"}],
            priority="high",
        )

        url = session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_per_request_low_priority(self):
        """Explicit low priority in request."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Background task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_default_priority_used(self):
        """When no priority specified, default_priority is used."""
        session = _make_mock_session()
        client = _make_client(session, default_priority="low")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_default_priority_high(self):
        """Client with high default priority."""
        session = _make_mock_session()
        client = _make_client(session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_per_request_overrides_default(self):
        """Per-request priority overrides client default."""
        session = _make_mock_session()
        client = _make_client(session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Low priority task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url


# ---------------------------------------------------------------------------
# Chat response parsing
# ---------------------------------------------------------------------------

class TestGatewayClientChat:
    """Test chat response parsing and payload construction."""

    async def test_chat_returns_parsed_response(self):
        """chat() returns dict with content, model, tokens, finish_reason."""
        session = _make_mock_session(response_json={
            "choices": [{"message": {"content": "Hello human!"}, "finish_reason": "stop"}],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 42},
        })
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["content"] == "Hello human!"
        assert result["model"] == "qwen3:8b"
        assert result["tokens_used"] == 42
        assert result["finish_reason"] == "stop"

    async def test_chat_sends_correct_payload(self):
        """chat() sends model, messages, temperature, max_tokens, top_p in payload."""
        session = _make_mock_session()
        client = _make_client(session, temperature=0.5, max_tokens=1000, top_p=0.85)

        await client.chat(
            messages=[{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "Hi"}],
        )

        call_kwargs = session.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["model"] == "qwen3:8b"
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 1000
        assert payload["top_p"] == 0.85
        assert len(payload["messages"]) == 2

    async def test_chat_per_request_overrides(self):
        """chat() uses per-request temperature/max_tokens/top_p when provided."""
        session = _make_mock_session()
        client = _make_client(session, temperature=0.7, max_tokens=2000, top_p=0.9)

        await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.1,
            max_tokens=500,
            top_p=0.5,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["temperature"] == 0.1
        assert payload["max_tokens"] == 500
        assert payload["top_p"] == 0.5

    async def test_chat_empty_choices(self):
        """chat() returns None when response has no choices."""
        session = _make_mock_session(response_json={
            "choices": [],
            "model": "qwen3:8b",
            "usage": {},
        })
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is None

    async def test_chat_missing_usage(self):
        """chat() handles missing usage field gracefully."""
        session = _make_mock_session(response_json={
            "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
            "model": "qwen3:8b",
        })
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["tokens_used"] == 0

    async def test_chat_missing_model_in_response(self):
        """chat() falls back to client model when missing in response."""
        session = _make_mock_session(response_json={
            "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        })
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["model"] == "qwen3:8b"

    async def test_chat_url_construction(self):
        """chat() constructs correct URL with base_url and priority."""
        session = _make_mock_session()
        client = _make_client(session, base_url="http://myhost:8200")

        await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
            priority="high",
        )

        url = session.post.call_args[0][0]
        assert url == "http://myhost:8200/v1/chat/completions?priority=high"

    async def test_chat_url_with_complexity(self):
        """chat() appends complexity param to URL when specified."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Complex task"}],
            priority="low",
            complexity="high",
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url
        assert "complexity=high" in url

    async def test_chat_url_without_complexity(self):
        """chat() omits complexity param when None."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Simple task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "complexity" not in url


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestGatewayClientErrors:
    """Test error handling in chat()."""

    async def test_chat_http_error(self):
        """chat() raises LLMConnectionError on HTTP error status."""
        session = _make_mock_session(response_status=500, response_text="Internal Server Error")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="500"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_chat_http_429_rate_limited(self):
        """chat() raises LLMConnectionError on HTTP 429 (rate limited)."""
        session = _make_mock_session(response_status=429, response_text="Too Many Requests")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="429"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_chat_timeout(self):
        """chat() raises LLMTimeoutError on asyncio.TimeoutError."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session, timeout_seconds=5)

        with pytest.raises(LLMTimeoutError, match="timeout"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_chat_connection_error(self):
        """chat() raises LLMConnectionError on aiohttp.ClientError."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError("Connection refused"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="connection error"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_chat_unexpected_exception(self):
        """chat() raises LLMConnectionError on unexpected exceptions."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=RuntimeError("Something unexpected"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="unexpected"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestGatewayClientHealthCheck:
    """Test health_check() method."""

    async def test_health_check_success(self):
        """health_check() returns True on HTTP 200."""
        session = _make_mock_session()
        client = _make_client(session)

        result = await client.health_check()

        assert result is True
        url = session.get.call_args[0][0]
        assert url == "http://127.0.0.1:8200/health"

    async def test_health_check_failure_status(self):
        """health_check() returns False on non-200 status."""
        health_response = AsyncMock()
        health_response.status = 503
        health_response.__aenter__ = AsyncMock(return_value=health_response)
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()

        assert result is False

    async def test_health_check_connection_error(self):
        """health_check() returns False on connection error."""
        health_response = AsyncMock()
        health_response.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError("Connection refused"),
        )
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()

        assert result is False

    async def test_health_check_timeout(self):
        """health_check() returns False on timeout."""
        health_response = AsyncMock()
        health_response.__aenter__ = AsyncMock(
            side_effect=asyncio.TimeoutError(),
        )
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()

        assert result is False


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestGatewayClientSession:
    """Test session management."""

    async def test_ensure_session_creates_when_none(self):
        """_ensure_session() creates a new session when none exists."""
        client = GatewayClient()
        assert client._session is None

        await client._ensure_session()

        assert client._session is not None
        # Clean up
        await client.close()

    async def test_ensure_session_creates_when_closed(self):
        """_ensure_session() creates a new session when current one is closed."""
        client = GatewayClient()
        # Create a mock closed session
        closed_session = MagicMock()
        closed_session.closed = True
        client._session = closed_session

        await client._ensure_session()

        # Should have replaced the closed session
        assert client._session is not closed_session
        # Clean up
        await client.close()

    async def test_ensure_session_reuses_open_session(self):
        """_ensure_session() reuses existing open session."""
        session = _make_mock_session()
        client = _make_client(session)

        await client._ensure_session()

        assert client._session is session

    async def test_close_session(self):
        """close() closes the session and sets it to None."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.close()

        session.close.assert_called_once()
        assert client._session is None

    async def test_close_when_no_session(self):
        """close() is a no-op when no session exists."""
        client = GatewayClient()
        # Should not raise
        await client.close()
        assert client._session is None

    async def test_close_when_session_already_closed(self):
        """close() is a no-op when session is already closed."""
        session = MagicMock()
        session.closed = True
        client = _make_client(session)

        await client.close()

        session.close.assert_not_called()


# ---------------------------------------------------------------------------
# LLMClient interface compliance
# ---------------------------------------------------------------------------

class TestGatewayClientInterface:
    """Verify GatewayClient implements the LLMClient abstract interface."""

    def test_implements_llm_client(self):
        """GatewayClient is a subclass of LLMClient."""
        from overblick.core.llm.client import LLMClient
        assert issubclass(GatewayClient, LLMClient)

    def test_has_chat_method(self):
        """GatewayClient has a chat method."""
        assert callable(getattr(GatewayClient, "chat", None))

    def test_has_health_check_method(self):
        """GatewayClient has a health_check method."""
        assert callable(getattr(GatewayClient, "health_check", None))

    def test_has_close_method(self):
        """GatewayClient has a close method."""
        assert callable(getattr(GatewayClient, "close", None))
