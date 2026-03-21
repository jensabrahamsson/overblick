"""
Tests for OllamaClient — local Ollama LLM inference.

Verifies:
- Initialization with exact default and custom parameters
- Chat response parsing (content, model, tokens, finish_reason)
- Qwen3 think-token stripping
- Health check (success, model not found, failure)
- Error handling (HTTP errors, timeout, connection error)
- Session lifecycle (lazy creation, close)
- Empty/malformed response handling
- Priority and complexity parameters ignored (no queue)
- Exact URL construction, payload keys, timeout values
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from overblick.core.exceptions import LLMConnectionError, LLMTimeoutError
from overblick.core.llm.ollama_client import OllamaClient


@pytest.fixture(autouse=True)
def _allow_direct_llm(monkeypatch):
    """Allow direct LLM client instantiation in tests."""
    monkeypatch.setenv("OVERBLICK_ALLOW_DIRECT_LLM", "1")


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
            "model": "qwen3.5:9b",
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
# Initialization — exact default values
# ---------------------------------------------------------------------------


class TestOllamaClientInit:
    def test_should_use_exact_default_base_url(self):
        client = OllamaClient()
        assert client.base_url == "http://localhost:11434/v1"

    def test_should_use_exact_default_model(self):
        client = OllamaClient()
        assert client.model == "qwen3.5:9b"

    def test_should_use_exact_default_max_tokens(self):
        client = OllamaClient()
        assert client.max_tokens == 2000

    def test_should_use_exact_default_temperature(self):
        client = OllamaClient()
        assert client.temperature == 0.7

    def test_should_use_exact_default_top_p(self):
        client = OllamaClient()
        assert client.top_p == 0.9

    def test_should_use_exact_default_timeout_seconds(self):
        client = OllamaClient()
        assert client.timeout_seconds == 600

    def test_should_have_none_session_at_init(self):
        client = OllamaClient()
        assert client._session is None

    def test_should_accept_custom_base_url(self):
        client = OllamaClient(base_url="http://gpu:11434/v1")
        assert client.base_url == "http://gpu:11434/v1"

    def test_should_accept_custom_model(self):
        client = OllamaClient(model="llama3:70b")
        assert client.model == "llama3:70b"

    def test_should_accept_custom_max_tokens(self):
        client = OllamaClient(max_tokens=4000)
        assert client.max_tokens == 4000

    def test_should_accept_custom_temperature(self):
        client = OllamaClient(temperature=0.5)
        assert client.temperature == 0.5

    def test_should_accept_custom_top_p(self):
        client = OllamaClient(top_p=0.95)
        assert client.top_p == 0.95

    def test_should_accept_custom_timeout(self):
        client = OllamaClient(timeout_seconds=300)
        assert client.timeout_seconds == 300

    def test_should_strip_trailing_slash(self):
        client = OllamaClient(base_url="http://localhost:11434/v1/")
        assert client.base_url == "http://localhost:11434/v1"

    def test_should_log_init_info(self, caplog):
        with caplog.at_level(logging.INFO):
            OllamaClient(model="test-model", base_url="http://test:11434/v1")
        assert "OllamaClient" in caplog.text
        assert "test-model" in caplog.text


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class TestOllamaClientChat:
    @pytest.mark.asyncio
    async def test_should_return_parsed_response(self):
        client = OllamaClient()
        client._session = _make_mock_session()

        result = await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert result is not None
        assert result["content"] == "Test response"
        assert result["model"] == "qwen3.5:9b"
        assert result["tokens_used"] == 42
        assert result["finish_reason"] == "stop"
        assert set(result.keys()) == {"content", "model", "tokens_used", "finish_reason"}

    @pytest.mark.asyncio
    async def test_should_send_default_params_in_payload(self):
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
        assert payload["model"] == "qwen3.5:9b"
        assert set(payload.keys()) == {"model", "messages", "temperature", "max_tokens", "top_p", "stream"}

    @pytest.mark.asyncio
    async def test_should_use_overridden_params(self):
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
    async def test_should_use_defaults_when_overrides_are_none(self):
        """Explicit None overrides fall back to instance defaults."""
        client = OllamaClient(temperature=0.7, max_tokens=2000, top_p=0.9)
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(
            messages=[{"role": "user", "content": "Test"}],
            temperature=None,
            max_tokens=None,
            top_p=None,
        )

        payload = mock_session.post.call_args[1]["json"]
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 2000
        assert payload["top_p"] == 0.9

    @pytest.mark.asyncio
    async def test_should_ignore_priority_param(self):
        """OllamaClient ignores priority (no queue)."""
        client = OllamaClient()
        mock_session = _make_mock_session()
        client._session = mock_session

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
            priority="high",
        )
        assert result is not None
        payload = mock_session.post.call_args[1]["json"]
        assert "priority" not in payload

    @pytest.mark.asyncio
    async def test_should_ignore_complexity_param(self):
        """OllamaClient ignores complexity parameter."""
        client = OllamaClient()
        mock_session = _make_mock_session()
        client._session = mock_session

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
            complexity="high",
        )
        assert result is not None
        payload = mock_session.post.call_args[1]["json"]
        assert "complexity" not in payload

    @pytest.mark.asyncio
    async def test_should_construct_correct_url(self):
        client = OllamaClient(base_url="http://myhost:11434/v1")
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(messages=[{"role": "user", "content": "Test"}])

        call_args = mock_session.post.call_args
        assert call_args[0][0] == "http://myhost:11434/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_should_return_none_for_empty_choices(self):
        client = OllamaClient()
        client._session = _make_mock_session(response_json={"choices": []})

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_should_use_timeout_seconds_in_request(self):
        client = OllamaClient(timeout_seconds=42)
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(messages=[{"role": "user", "content": "Test"}])

        call_kwargs = mock_session.post.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 42

    @pytest.mark.asyncio
    async def test_should_set_stream_false_in_payload(self):
        """Payload includes stream=False."""
        client = OllamaClient()
        mock_session = _make_mock_session()
        client._session = mock_session

        await client.chat(messages=[{"role": "user", "content": "Test"}])

        payload = mock_session.post.call_args[1]["json"]
        assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_should_raise_llm_connection_error_on_http_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            response_status=500, response_text="Internal Server Error"
        )

        with pytest.raises(LLMConnectionError, match="Ollama API error 500"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_should_check_status_not_equal_200(self):
        """Non-200 status triggers error. Tests != 200 comparison."""
        client = OllamaClient()
        client._session = _make_mock_session(response_status=201, response_text="Created")

        with pytest.raises(LLMConnectionError, match="201"):
            await client.chat(messages=[{"role": "user", "content": "Test"}])

    @pytest.mark.asyncio
    async def test_should_truncate_error_text_to_200_chars(self):
        long_error = "Z" * 500
        client = OllamaClient()
        client._session = _make_mock_session(response_status=500, response_text=long_error)

        with pytest.raises(LLMConnectionError) as exc_info:
            await client.chat(messages=[{"role": "user", "content": "Test"}])
        assert str(exc_info.value).count("Z") <= 200

    @pytest.mark.asyncio
    async def test_should_raise_llm_timeout_error_on_timeout(self):
        client = OllamaClient(timeout_seconds=7)
        client._session = _make_mock_session(post_side_effect=TimeoutError())

        with pytest.raises(LLMTimeoutError, match="Ollama request timeout") as exc_info:
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )
        assert "7s" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_should_raise_llm_connection_error_on_client_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=aiohttp.ClientError("Connection refused")
        )

        with pytest.raises(LLMConnectionError, match="Ollama connection error"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_should_raise_llm_connection_error_on_unexpected_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(post_side_effect=RuntimeError("Something broke"))

        with pytest.raises(LLMConnectionError, match="Ollama unexpected error"):
            await client.chat(
                messages=[{"role": "user", "content": "Test"}],
            )

    @pytest.mark.asyncio
    async def test_should_reraise_llm_timeout_error(self):
        """LLMTimeoutError should pass through without wrapping."""
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=LLMTimeoutError("already wrapped")
        )

        with pytest.raises(LLMTimeoutError, match="already wrapped"):
            await client.chat(messages=[{"role": "user", "content": "Test"}])

    @pytest.mark.asyncio
    async def test_should_reraise_llm_connection_error(self):
        """LLMConnectionError should pass through without wrapping."""
        client = OllamaClient()
        client._session = _make_mock_session(
            post_side_effect=LLMConnectionError("already wrapped")
        )

        with pytest.raises(LLMConnectionError, match="already wrapped"):
            await client.chat(messages=[{"role": "user", "content": "Test"}])

    @pytest.mark.asyncio
    async def test_should_handle_missing_usage_with_zero_tokens(self):
        """Missing usage field defaults to 0 tokens."""
        client = OllamaClient()
        client._session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                "model": "qwen3:8b",
            }
        )

        result = await client.chat(messages=[{"role": "user", "content": "Test"}])
        assert result is not None
        assert result["tokens_used"] == 0

    @pytest.mark.asyncio
    async def test_should_use_client_model_in_result(self):
        """Result model comes from self.model, not the response."""
        client = OllamaClient(model="my-model")
        client._session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                "model": "different-model",
                "usage": {"total_tokens": 10},
            }
        )

        result = await client.chat(messages=[{"role": "user", "content": "Test"}])
        assert result is not None
        assert result["model"] == "my-model"

    @pytest.mark.asyncio
    async def test_should_get_finish_reason_from_first_choice(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "length"}],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )

        result = await client.chat(messages=[{"role": "user", "content": "Test"}])
        assert result is not None
        assert result["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Think token stripping
# ---------------------------------------------------------------------------


class TestThinkTokenStripping:
    def test_should_strip_single_think_block(self):
        text = "<think>I need to reason about this</think>The actual answer"
        assert OllamaClient.strip_think_tokens(text) == "The actual answer"

    def test_should_strip_multiline_think_block(self):
        text = (
            "<think>\nStep 1: Consider options\nStep 2: Choose best\n</think>\nHere is my response."
        )
        assert OllamaClient.strip_think_tokens(text) == "Here is my response."

    def test_should_strip_multiple_think_blocks(self):
        text = "<think>first</think>Hello <think>second</think>World"
        assert OllamaClient.strip_think_tokens(text) == "Hello World"

    def test_should_return_unchanged_when_no_think_tokens(self):
        text = "Just a plain response with no thinking"
        assert OllamaClient.strip_think_tokens(text) == text

    def test_should_strip_empty_think_block(self):
        text = "<think></think>Response"
        assert OllamaClient.strip_think_tokens(text) == "Response"

    def test_should_handle_empty_string(self):
        assert OllamaClient.strip_think_tokens("") == ""

    @pytest.mark.asyncio
    async def test_should_strip_think_tokens_in_chat_flow(self):
        """Verify think tokens are stripped in actual chat flow."""
        response_json = {
            "choices": [
                {
                    "message": {"content": "<think>reasoning</think>Actual answer"},
                    "finish_reason": "stop",
                }
            ],
            "model": "qwen3.5:9b",
            "usage": {"total_tokens": 50},
        }
        client = OllamaClient()
        client._session = _make_mock_session(response_json=response_json)

        result = await client.chat(
            messages=[{"role": "user", "content": "Test"}],
        )
        assert result is not None
        assert result["content"] == "Actual answer"

    @pytest.mark.asyncio
    async def test_should_log_think_chars_when_stripped(self, caplog):
        """When think tokens are stripped, log shows reasoning char count."""
        response_json = {
            "choices": [
                {
                    "message": {"content": "<think>reasoning text</think>Answer"},
                    "finish_reason": "stop",
                }
            ],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 50},
        }
        client = OllamaClient()
        client._session = _make_mock_session(response_json=response_json)

        with caplog.at_level(logging.INFO):
            await client.chat(messages=[{"role": "user", "content": "Test"}])

        assert "reasoning" in caplog.text

    @pytest.mark.asyncio
    async def test_should_log_normal_response_when_no_think_tokens(self, caplog):
        """When no think tokens, log shows normal char count."""
        response_json = {
            "choices": [
                {
                    "message": {"content": "Normal answer"},
                    "finish_reason": "stop",
                }
            ],
            "model": "qwen3:8b",
            "usage": {"total_tokens": 50},
        }
        client = OllamaClient()
        client._session = _make_mock_session(response_json=response_json)

        with caplog.at_level(logging.INFO):
            await client.chat(messages=[{"role": "user", "content": "Test"}])

        assert "chars" in caplog.text


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestOllamaClientHealth:
    @pytest.mark.asyncio
    async def test_should_return_true_when_model_found(self):
        health_response = {
            "models": [
                {"name": "qwen3.5:9b"},
                {"name": "llama3:8b"},
            ]
        }
        client = OllamaClient(model="qwen3.5:9b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_should_return_false_when_model_not_found(self):
        health_response = {
            "models": [
                {"name": "llama3:8b"},
            ]
        }
        client = OllamaClient(model="qwen3.5:9b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_match_model_base_name(self):
        """Model base name matches even with different tag."""
        health_response = {
            "models": [
                {"name": "qwen3.5:latest"},
            ]
        }
        client = OllamaClient(model="qwen3.5:9b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_should_split_model_on_colon(self):
        """Model name is split on ':' to get base name."""
        health_response = {
            "models": [
                {"name": "deepseek-r1:32b"},
            ]
        }
        client = OllamaClient(model="deepseek-r1:7b")
        client._session = _make_mock_session(response_json=health_response)

        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_should_return_false_on_api_failure(self):
        client = OllamaClient()
        client._session = _make_mock_session(response_status=500)

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_check_status_equals_200(self):
        """Non-200 status returns False. Tests == 200 comparison."""
        mock_response = AsyncMock()
        mock_response.status = 201
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=mock_response)
        client = OllamaClient()
        client._session = session

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_return_false_on_connection_error(self):
        client = OllamaClient()
        client._session = _make_mock_session(
            get_side_effect=aiohttp.ClientError("Connection refused")
        )

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_construct_correct_health_url(self):
        """Health check uses /api/tags endpoint (strips /v1)."""
        client = OllamaClient(base_url="http://myhost:11434/v1")
        mock_session = _make_mock_session(response_json={"models": [{"name": "qwen3.5:9b"}]})
        client._session = mock_session

        await client.health_check()

        call_args = mock_session.get.call_args
        assert call_args[0][0] == "http://myhost:11434/api/tags"

    @pytest.mark.asyncio
    async def test_should_use_5_second_timeout_for_health(self):
        """Health check uses 5 second timeout."""
        client = OllamaClient()
        mock_session = _make_mock_session(response_json={"models": [{"name": "qwen3:8b"}]})
        client._session = mock_session

        await client.health_check()

        call_kwargs = mock_session.get.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 5

    @pytest.mark.asyncio
    async def test_should_handle_empty_models_list(self):
        """Empty models list means model not found."""
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json={"models": []})

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_handle_missing_models_key(self):
        """Missing 'models' key defaults to empty list."""
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json={})

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_handle_model_with_missing_name(self):
        """Model entries with no 'name' key yield None."""
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json={"models": [{"size": "8b"}]})

        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_log_warning_when_model_not_found(self, caplog):
        """When model isn't found, a warning is logged."""
        client = OllamaClient(model="qwen3:8b")
        client._session = _make_mock_session(response_json={"models": [{"name": "llama3:8b"}]})

        with caplog.at_level(logging.WARNING):
            await client.health_check()

        assert "not found" in caplog.text


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestOllamaClientSession:
    @pytest.mark.asyncio
    async def test_should_create_session_when_none(self):
        client = OllamaClient()
        assert client._session is None

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._ensure_session()
            mock_cls.assert_called_once()
            assert client._session is not None

    @pytest.mark.asyncio
    async def test_should_reuse_existing_open_session(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = False
        client._session = mock_session

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            await client._ensure_session()
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_recreate_when_session_closed(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session

        with patch("overblick.core.llm.ollama_client.aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._ensure_session()
            mock_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_close_and_set_none(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.close()

        mock_session.close.assert_called_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_should_noop_close_when_no_session(self):
        client = OllamaClient()
        await client.close()

    @pytest.mark.asyncio
    async def test_should_noop_close_when_already_closed(self):
        client = OllamaClient()
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session

        await client.close()
        # Should not try to close an already-closed session
