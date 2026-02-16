"""
Tests for OllamaClient — local Ollama LLM inference.

Verifies:
- Initialization with default and custom parameters
- Chat response parsing (content, model, tokens, finish_reason)
- Qwen3 think-token stripping
- Health check (success, model not found, failure)
- Error handling (HTTP errors, timeout, connection error)
- Session lifecycle (lazy creation, close)
- Empty/malformed response handling
- Priority parameter ignored (no queue)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from overblick.core.exceptions import LLMConnectionError, LLMTimeoutError
from overblick.core.llm.ollama_client import OllamaClient


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
        session.get = MagicMock(return_value=mock_response)

    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestOllamaClientInit:
    def test_default_init(self):
        client = OllamaClient()
        assert client.base_url == "http://localhost:11434/v1"
        assert client.model == "qwen3:8b"
        assert client.max_tokens == 2000
        assert client.temperature == 0.7
        assert client.top_p == 0.9
        assert client.timeout_seconds == 180
        assert client._session is None

    def test_custom_init(self):
        client = OllamaClient(
            base_url="http://gpu:11434/v1/",
            model="llama3:70b",
            max_tokens=4000,
            temperature=0.5,
            top_p=0.95,
            timeout_seconds=300,
        )
        assert client.base_url == "http://gpu:11434/v1"
        assert client.model == "llama3:70b"
        assert client.max_tokens == 4000
        assert client.temperature == 0.5
        assert client.top_p == 0.95
        assert client.timeout_seconds == 300

    def test_trailing_slash_stripped(self):
        client = OllamaClient(base_url="http://localhost:11434/v1/")
        assert client.base_url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class TestOllamaClientChat:
    @pytest.mark.asyncio
    async def test_basic_chat(self):
        client = OllamaClient()
        client._session = _make_mock_session()

        result = await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert result is not None
        assert result["content"] == "Test response"
        assert result["model"] == "qwen3:8b"
        assert result["tokens_used"] == 42
        assert result["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_chat_uses_default_params(self):
        client = OllamaClient(temperature=0.5, max_tokens=1000, top_p=0.85)
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(messages=[{"role": "user", "content": "Test"}])

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 1000
        assert payload["top_p"] == 0.85
        assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_chat_override_params(self):
        client = OllamaClient()
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(
            messages=[{"role": "user", "content": "Test"}],
            temperature=0.9,
            max_tokens=500,
            top_p=0.8,
        )

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert payload["temperature"] == 0.9
        assert payload["max_tokens"] == 500
        assert payload["top_p"] == 0.8

    @pytest.mark.asyncio
    async def test_chat_priority_ignored(self):
        """OllamaClient ignores priority (no queue)."""
        client = OllamaClient()
        mock_session = _make_mock_session()
        client._session = mock_session

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
            priority="high",
        )
        assert result is not None
        # Priority should not appear in payload
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert "priority" not in payload

    @pytest.mark.asyncio
    async def test_chat_correct_url(self):
        client = OllamaClient(base_url="http://myhost:11434/v1")
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(messages=[{"role": "user", "content": "Test"}])

        call_args = mock_session.post.call_args
        assert call_args[0][0] == "http://myhost:11434/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_chat_empty_choices(self):
        client = OllamaClient()
        client._session = _make_mock_session(response_json={"choices": []})

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_http_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            response_status=500, response_text="Internal Server Error"
        )

        with pytest.raises(LLMConnectionError, match="500"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_timeout(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=asyncio.TimeoutError()
        )

        with pytest.raises(LLMTimeoutError, match="timeout"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_connection_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=aiohttp.ClientError("Connection refused")
        )

        with pytest.raises(LLMConnectionError, match="connection error"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_chat_unexpected_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=RuntimeError("Something broke")
        )

        with pytest.raises(LLMConnectionError, match="unexpected"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )


# ---------------------------------------------------------------------------
# Think token stripping
# ---------------------------------------------------------------------------

class TestThinkTokenStripping:
    def test_strip_think_tokens(self):
        text = "<think>I need to reason about this</think>The actual answer"
        assert OllamaClient.strip_think_tokens(text) == "The actual answer"

    def test_strip_multiline_think(self):
        text = (
            "<think>\nStep 1: Consider options\nStep 2: Choose best\n</think>\n"
            "Here is my response."
        )
        assert OllamaClient.strip_think_tokens(text) == "Here is my response."

    def test_strip_multiple_think_blocks(self):
        text = "<think>first</think>Hello <think>second</think>World"
        assert OllamaClient.strip_think_tokens(text) == "Hello World"

    def test_no_think_tokens(self):
        text = "Just a plain response with no thinking"
        assert OllamaClient.strip_think_tokens(text) == text

    def test_empty_think_block(self):
        text = "<think></think>Response"
        assert OllamaClient.strip_think_tokens(text) == "Response"

    def test_empty_string(self):
        assert OllamaClient.strip_think_tokens("") == ""

    @pytest.mark.asyncio
    async def test_chat_strips_think_tokens(self):
        """Verify think tokens are stripped in actual chat flow."""
        response_json = {
            "choices": [{
                "message": {"content": "<think>reasoning</think>Actual answer"},
                "finish_reason": "stop",
            }],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 50},
        }
        client = OllamaClient()
        client._session = _make_mock_session(response_json=response_json)

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
        )
        assert result is not None
        assert result["content"] == "Actual answer"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestOllamaClientHealth:
    @pytest.mark.asyncio
    async def test_health_check_success(self):
        health_response = {
            "models": [
                {"name": "qwen3:8b"},
                {"name": "llama3:8b"},
            ]
        }
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_model_not_found(self):
        health_response = {
            "models": [
                {"name": "llama3:8b"},
            ]
        }
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_model_version_match(self):
        """Model base name matches even with different tag."""
        health_response = {
            "models": [
                {"name": "qwen3:latest"},
            ]
        }
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_api_failure(self):
        client = OllamaClient()
        client._session = _make_mock_session(response_status=500)

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            get_side_effect=aiohttp.ClientError("Connection refused")
        )

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_correct_url(self):
        """Health check uses /api/tags endpoint (not /v1/)."""
        client = OllamaClient(base_url="http://myhost:11434/v1")
        mock_session = _make_mock_session(
            response_json={"models": [{"name": "qwen3:8b"}]}
        )
        client._session = mock_session

        await client.health_check()

        call_args = mock_session.get.call_args
        assert call_args[0][0] == "http://myhost:11434/api/tags"


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestOllamaClientSession:
    @pytest.mark.asyncio
    async def test_ensure_session_creates(self):
        client = OllamaClient()
        assert client._session is None

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._ensure_session()
            mock_cls.assert_called_once()
            assert client._session is not None

    @pytest.mark.asyncio
    async def test_ensure_session_reuses(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = False
        client._session = mock_session

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            await client._ensure_session()
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_session_recreates_when_closed(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._ensure_session()
            mock_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_close(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.close()

        mock_session.close.assert_called_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_no_session(self):
        client = OllamaClient()
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session

        await client.close()
        # Should not call close on already-closed session
