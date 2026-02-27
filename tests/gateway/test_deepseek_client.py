"""
Tests for the Deepseek API client.

Verifies:
- Client initialization with defaults and custom params
- Chat completion: request construction, response parsing, error handling
- Health check: success, failure, exception
- Model listing: success, connection error, empty response
- Session lifecycle: lazy creation, close, auth headers
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from overblick.gateway.deepseek_client import (
    DeepseekClient,
    DeepseekError,
    DeepseekConnectionError,
    DeepseekTimeoutError,
)
from overblick.gateway.models import (
    ChatRequest,
    ChatMessage,
    ChatResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_response_data(content="Hello!", model="deepseek-chat"):
    """Create mock Deepseek API response JSON."""
    return {
        "id": "chatcmpl-test123",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def _make_request(model="deepseek-chat", content="Hello!"):
    """Create a sample ChatRequest."""
    return ChatRequest(
        model=model,
        messages=[ChatMessage(role="user", content=content)],
        max_tokens=1000,
        temperature=0.7,
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestDeepseekClientInit:
    def test_default_parameters(self):
        client = DeepseekClient()
        assert client.api_url == "https://api.deepseek.com/v1"
        assert client.api_key == ""
        assert client.model == "deepseek-chat"
        assert client.timeout_seconds == 300.0
        assert client._client is None

    def test_custom_parameters(self):
        client = DeepseekClient(
            api_url="https://custom.api.com/v1/",
            api_key="sk-test123",
            model="deepseek-coder",
            timeout_seconds=60.0,
        )
        assert client.api_url == "https://custom.api.com/v1"
        assert client.api_key == "sk-test123"
        assert client.model == "deepseek-coder"

    def test_trailing_slash_stripped(self):
        client = DeepseekClient(api_url="https://api.deepseek.com/v1/")
        assert client.api_url == "https://api.deepseek.com/v1"


# ---------------------------------------------------------------------------
# Chat completion
# ---------------------------------------------------------------------------

class TestDeepseekChatCompletion:
    @pytest.mark.asyncio
    async def test_successful_chat(self):
        """chat_completion returns parsed ChatResponse."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response_data("Hi there!")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        request = _make_request()
        response = await client.chat_completion(request)

        assert isinstance(response, ChatResponse)
        assert response.choices[0].message.content == "Hi there!"
        assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self):
        """chat_completion sends correct JSON payload."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response_data()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        request = _make_request(content="Test message")
        await client.chat_completion(request)

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/chat/completions"
        payload = call_args[1]["json"]
        assert payload["model"] == "deepseek-chat"
        assert payload["messages"][0]["content"] == "Test message"
        assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """chat_completion raises DeepseekConnectionError on ConnectError."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        with pytest.raises(DeepseekConnectionError, match="Cannot connect"):
            await client.chat_completion(_make_request())

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """chat_completion raises DeepseekTimeoutError on timeout."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        with pytest.raises(DeepseekTimeoutError, match="timed out"):
            await client.chat_completion(_make_request())

    @pytest.mark.asyncio
    async def test_http_error(self):
        """chat_completion raises DeepseekError on HTTP error status."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=MagicMock(), response=mock_response
            )
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        with pytest.raises(DeepseekError, match="429"):
            await client.chat_completion(_make_request())

    @pytest.mark.asyncio
    async def test_unexpected_error(self):
        """chat_completion raises DeepseekError on unexpected exception."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        with pytest.raises(DeepseekError, match="Failed to call"):
            await client.chat_completion(_make_request())

    @pytest.mark.asyncio
    async def test_empty_choices_returns_fallback(self):
        """chat_completion returns fallback message when no choices."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-x",
            "model": "deepseek-chat",
            "choices": [],
            "usage": {},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        response = await client.chat_completion(_make_request())
        assert response.choices[0].message.content == "No response generated"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestDeepseekHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_with_key(self):
        """Cloud backend with API key is healthy (no HTTP call)."""
        client = DeepseekClient(api_key="sk-test")
        assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_without_key(self):
        """Cloud backend without API key is unhealthy."""
        client = DeepseekClient(api_key="")
        assert await client.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_with_bad_key_still_healthy(self):
        """Even an invalid key makes the backend 'configured' (healthy)."""
        client = DeepseekClient(api_key="bad-key")
        assert await client.health_check() is True


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------

class TestDeepseekListModels:
    @pytest.mark.asyncio
    async def test_list_models_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"id": "deepseek-chat"}, {"id": "deepseek-coder"}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        models = await client.list_models()
        assert models == ["deepseek-chat", "deepseek-coder"]

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        with pytest.raises(DeepseekConnectionError):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_list_models_empty(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        models = await client.list_models()
        assert models == []


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestDeepseekClientSession:
    @pytest.mark.asyncio
    async def test_get_client_creates_with_auth(self):
        """_get_client creates httpx client with Bearer auth header."""
        client = DeepseekClient(api_key="sk-test123")
        http_client = await client._get_client()
        assert http_client is not None
        assert "Authorization" in http_client.headers
        assert http_client.headers["Authorization"] == "Bearer sk-test123"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_no_auth_when_no_key(self):
        """_get_client creates httpx client without auth when no key."""
        client = DeepseekClient(api_key="")
        http_client = await client._get_client()
        assert "Authorization" not in http_client.headers
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self):
        """close() closes the httpx client."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()

        client = DeepseekClient()
        client._client = mock_client
        await client.close()
        mock_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_none(self):
        """close() is a no-op when no client exists."""
        client = DeepseekClient()
        await client.close()  # Should not raise
        assert client._client is None


# ---------------------------------------------------------------------------
# Health check — new behavior (api_key-based, no HTTP call)
# ---------------------------------------------------------------------------

class TestDeepseekHealthCheckNew:
    """New health_check behavior: returns bool(api_key), no HTTP call."""

    @pytest.mark.asyncio
    async def test_health_check_with_api_key(self):
        """Cloud backend with API key is considered healthy (no HTTP call needed)."""
        client = DeepseekClient(api_key="sk-test123")
        assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_without_api_key(self):
        """Cloud backend without API key is unhealthy."""
        client = DeepseekClient(api_key="")
        assert await client.health_check() is False


# ---------------------------------------------------------------------------
# Connectivity check (the old HTTP-based reachability test)
# ---------------------------------------------------------------------------

class TestDeepseekConnectivityCheck:
    """connectivity_check() does the actual HTTP call to /models."""

    @pytest.mark.asyncio
    async def test_connectivity_check_success(self):
        """connectivity_check returns True when API responds 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        assert await client.connectivity_check() is True

    @pytest.mark.asyncio
    async def test_connectivity_check_failure(self):
        """connectivity_check returns False on connection error."""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        client = DeepseekClient(api_key="sk-test")
        client._client = mock_client

        assert await client.connectivity_check() is False
