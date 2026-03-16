"""
Tests for the GatewayClient LLM interface.

Verifies:
- Initialization with default and custom parameters (exact values)
- Priority handling (default, per-request, override)
- Chat response parsing (content, model, tokens, finish_reason)
- Health check (success, failure, exception)
- Error handling (HTTP errors, timeout, connection error, unexpected errors)
- Session lifecycle (lazy creation, close)
- Empty/malformed response handling
- Embed endpoint
- Exact URL construction, payload keys, timeout values
"""

import logging
from unittest.mock import AsyncMock, MagicMock

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
# Initialization — exact default values
# ---------------------------------------------------------------------------


class TestGatewayClientInit:
    """Test GatewayClient initialization with exact value assertions."""

    def test_should_use_exact_default_base_url(self):
        client = GatewayClient()
        assert client.base_url == "http://127.0.0.1:8200"

    def test_should_use_exact_default_model(self):
        client = GatewayClient()
        assert client.model == "qwen3:8b"

    def test_should_use_exact_default_priority(self):
        client = GatewayClient()
        assert client.default_priority == "low"

    def test_should_use_exact_default_max_tokens(self):
        client = GatewayClient()
        assert client.max_tokens == 2000

    def test_should_use_exact_default_temperature(self):
        client = GatewayClient()
        assert client.temperature == 0.7

    def test_should_use_exact_default_top_p(self):
        client = GatewayClient()
        assert client.top_p == 0.9

    def test_should_use_exact_default_timeout_seconds(self):
        client = GatewayClient()
        assert client.timeout_seconds == 300

    def test_should_have_none_session_at_init(self):
        client = GatewayClient()
        assert client._session is None

    def test_should_accept_custom_base_url(self):
        client = GatewayClient(base_url="http://custom:9999")
        assert client.base_url == "http://custom:9999"

    def test_should_accept_custom_model(self):
        client = GatewayClient(model="llama3:70b")
        assert client.model == "llama3:70b"

    def test_should_accept_custom_priority(self):
        client = GatewayClient(default_priority="high")
        assert client.default_priority == "high"

    def test_should_accept_custom_max_tokens(self):
        client = GatewayClient(max_tokens=4096)
        assert client.max_tokens == 4096

    def test_should_accept_custom_temperature(self):
        client = GatewayClient(temperature=0.3)
        assert client.temperature == 0.3

    def test_should_accept_custom_top_p(self):
        client = GatewayClient(top_p=0.95)
        assert client.top_p == 0.95

    def test_should_accept_custom_timeout(self):
        client = GatewayClient(timeout_seconds=120)
        assert client.timeout_seconds == 120

    def test_should_strip_trailing_slash_from_base_url(self):
        client = GatewayClient(base_url="http://example.com:8200/")
        assert client.base_url == "http://example.com:8200"

    def test_should_store_base_url_after_rstrip(self):
        """Verifies rstrip('/') is applied to base_url."""
        client = GatewayClient(base_url="http://host:8200///")
        assert not client.base_url.endswith("/")

    def test_should_log_init_info(self, caplog):
        """Verify logger.info is called during init."""
        with caplog.at_level(logging.INFO):
            GatewayClient(
                base_url="http://test:8200",
                default_priority="high",
                model="test-model",
            )
        assert "GatewayClient" in caplog.text
        assert "test-model" in caplog.text


# ---------------------------------------------------------------------------
# Priority handling
# ---------------------------------------------------------------------------


class TestGatewayClientPriority:
    """Tests for GatewayClient priority handling."""

    async def test_should_use_per_request_high_priority(self):
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Urgent!"}],
            priority="high",
        )

        url = session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_should_use_per_request_low_priority(self):
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Background task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_should_use_default_priority_when_empty_string(self):
        """When priority is empty string (default), default_priority is used."""
        session = _make_mock_session()
        client = _make_client(session, default_priority="low")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_should_use_high_default_priority(self):
        session = _make_mock_session()
        client = _make_client(session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )

        url = session.post.call_args[0][0]
        assert "priority=high" in url

    async def test_should_override_default_with_per_request_priority(self):
        session = _make_mock_session()
        client = _make_client(session, default_priority="high")

        await client.chat(
            messages=[{"role": "user", "content": "Low priority task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "priority=low" in url

    async def test_should_use_empty_priority_default_param(self):
        """The default value of priority param is empty string."""
        session = _make_mock_session()
        client = _make_client(session, default_priority="low")

        # Call without priority= kwarg - it defaults to ""
        await client.chat(messages=[{"role": "user", "content": "Hi"}])

        url = session.post.call_args[0][0]
        # Empty string is falsy, so default_priority="low" is used
        assert "priority=low" in url


# ---------------------------------------------------------------------------
# Chat response parsing
# ---------------------------------------------------------------------------


class TestGatewayClientChat:
    """Test chat response parsing and payload construction."""

    async def test_should_return_parsed_response_with_all_keys(self):
        session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Hello human!"}, "finish_reason": "stop"}],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 42},
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["content"] == "Hello human!"
        assert result["model"] == "qwen3:8b"
        assert result["tokens_used"] == 42
        assert result["finish_reason"] == "stop"
        # Verify exactly 4 keys (or 4+reasoning when present)
        assert set(result.keys()) == {"content", "model", "tokens_used", "finish_reason"}

    async def test_should_send_correct_payload_keys(self):
        session = _make_mock_session()
        client = _make_client(session, temperature=0.5, max_tokens=1000, top_p=0.85)

        await client.chat(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        )

        call_kwargs = session.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["model"] == "qwen3:8b"
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 1000
        assert payload["top_p"] == 0.85
        assert len(payload["messages"]) == 2
        # Ensure exact payload keys
        assert set(payload.keys()) == {"model", "messages", "temperature", "max_tokens", "top_p"}

    async def test_should_use_per_request_temperature_override(self):
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

    async def test_should_use_defaults_when_overrides_are_none(self):
        """When temperature/max_tokens/top_p are None, defaults from __init__ are used."""
        session = _make_mock_session()
        client = _make_client(session, temperature=0.7, max_tokens=2000, top_p=0.9)

        await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=None,
            max_tokens=None,
            top_p=None,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 2000
        assert payload["top_p"] == 0.9

    async def test_should_return_none_when_no_choices(self):
        session = _make_mock_session(
            response_json={
                "choices": [],
                "model": "qwen3:8b",
                "usage": {},
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is None

    async def test_should_handle_missing_usage_with_zero_tokens(self):
        session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
                "model": "qwen3:8b",
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["tokens_used"] == 0

    async def test_should_fallback_to_client_model_when_missing_in_response(self):
        session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Response"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert result["model"] == "qwen3:8b"

    async def test_should_construct_correct_url_with_base_url_and_priority(self):
        session = _make_mock_session()
        client = _make_client(session, base_url="http://myhost:8200")

        await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
            priority="high",
        )

        url = session.post.call_args[0][0]
        assert url == "http://myhost:8200/v1/chat/completions?priority=high"

    async def test_should_append_complexity_param_to_url(self):
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
        assert url.startswith("http://127.0.0.1:8200/v1/chat/completions?")

    async def test_should_omit_complexity_when_none(self):
        session = _make_mock_session()
        client = _make_client(session)

        await client.chat(
            messages=[{"role": "user", "content": "Simple task"}],
            priority="low",
        )

        url = session.post.call_args[0][0]
        assert "complexity" not in url

    async def test_should_use_timeout_seconds_in_request(self):
        """Verify that self.timeout_seconds is passed to aiohttp.ClientTimeout."""
        session = _make_mock_session()
        client = _make_client(session, timeout_seconds=42)

        await client.chat(messages=[{"role": "user", "content": "Hi"}])

        call_kwargs = session.post.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 42

    async def test_should_strip_think_tokens_from_response(self):
        """Verify that Qwen3 <think> tokens are stripped from content."""
        session = _make_mock_session(
            response_json={
                "choices": [
                    {
                        "message": {"content": "<think>reasoning</think>Actual answer"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 50},
            }
        )
        client = _make_client(session)

        result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result is not None
        assert result["content"] == "Actual answer"
        assert "<think>" not in result["content"]

    async def test_should_handle_missing_message_key_in_choices(self):
        """When choices[0] has no 'message' key, message defaults to {}."""
        session = _make_mock_session(
            response_json={
                "choices": [{"finish_reason": "stop"}],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result is not None
        assert result["content"] == ""

    async def test_should_handle_missing_content_key_in_message(self):
        """When message has no 'content' key, content defaults to ''."""
        session = _make_mock_session(
            response_json={
                "choices": [{"message": {}, "finish_reason": "stop"}],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result is not None
        assert result["content"] == ""

    async def test_should_get_finish_reason_from_first_choice(self):
        """finish_reason is read from choices[0]."""
        session = _make_mock_session(
            response_json={
                "choices": [
                    {"message": {"content": "Hi"}, "finish_reason": "length"},
                ],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result is not None
        assert result["finish_reason"] == "length"

    async def test_should_read_total_tokens_from_nested_usage(self):
        """tokens_used comes from usage.total_tokens."""
        session = _make_mock_session(
            response_json={
                "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 999, "prompt_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert result is not None
        assert result["tokens_used"] == 999


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestGatewayClientErrors:
    """Test error handling in chat()."""

    async def test_should_raise_llm_connection_error_on_http_500(self):
        session = _make_mock_session(response_status=500, response_text="Internal Server Error")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="Gateway API error 500"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_should_raise_llm_connection_error_on_http_429(self):
        session = _make_mock_session(response_status=429, response_text="Too Many Requests")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="429"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_should_check_status_not_equal_200(self):
        """Non-200 status codes trigger error path. Ensures != 200 comparison works."""
        session = _make_mock_session(response_status=201, response_text="Created")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="201"):
            await client.chat(messages=[{"role": "user", "content": "Hi"}])

    async def test_should_truncate_error_text_to_200_chars(self):
        """Error text in exception message is truncated at 200 chars."""
        long_error = "X" * 500
        session = _make_mock_session(response_status=500, response_text=long_error)
        client = _make_client(session)

        with pytest.raises(LLMConnectionError) as exc_info:
            await client.chat(messages=[{"role": "user", "content": "Hi"}])

        # The error message should contain at most 200 X's
        error_msg = str(exc_info.value)
        assert error_msg.count("X") <= 200

    async def test_should_raise_llm_timeout_error_on_timeout(self):
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=TimeoutError())
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session, timeout_seconds=5)

        with pytest.raises(LLMTimeoutError, match="Gateway request timeout") as exc_info:
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )
        assert "5s" in str(exc_info.value)

    async def test_should_raise_llm_connection_error_on_client_error(self):
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError("Connection refused"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="Gateway connection error"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_should_raise_llm_connection_error_on_unexpected_exception(self):
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=RuntimeError("Something unexpected"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="Gateway unexpected error"):
            await client.chat(
                messages=[{"role": "user", "content": "Hi"}],
            )

    async def test_should_reraise_llm_timeout_error_directly(self):
        """LLMTimeoutError should pass through without wrapping."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=LLMTimeoutError("already wrapped"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)
        client = _make_client(session)

        with pytest.raises(LLMTimeoutError, match="already wrapped"):
            await client.chat(messages=[{"role": "user", "content": "Hi"}])

    async def test_should_reraise_llm_connection_error_directly(self):
        """LLMConnectionError should pass through without wrapping."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=LLMConnectionError("already wrapped"),
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="already wrapped"):
            await client.chat(messages=[{"role": "user", "content": "Hi"}])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestGatewayClientHealthCheck:
    """Test health_check() method."""

    async def test_should_return_true_on_http_200(self):
        session = _make_mock_session()
        client = _make_client(session)

        result = await client.health_check()

        assert result is True
        url = session.get.call_args[0][0]
        assert url == "http://127.0.0.1:8200/health"

    async def test_should_construct_correct_health_url(self):
        """URL is base_url + '/health'."""
        session = _make_mock_session()
        client = _make_client(session, base_url="http://custom:9999")

        await client.health_check()

        url = session.get.call_args[0][0]
        assert url == "http://custom:9999/health"

    async def test_should_use_5_second_timeout_for_health(self):
        """Health check uses a 5-second timeout."""
        session = _make_mock_session()
        client = _make_client(session)

        await client.health_check()

        call_kwargs = session.get.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 5

    async def test_should_return_false_on_non_200_status(self):
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

    async def test_should_check_status_equals_200(self):
        """Verifies exact 200 comparison — 201 should return False."""
        health_response = AsyncMock()
        health_response.status = 201
        health_response.__aenter__ = AsyncMock(return_value=health_response)
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()
        assert result is False

    async def test_should_return_false_on_connection_error(self):
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

    async def test_should_return_false_on_timeout(self):
        health_response = AsyncMock()
        health_response.__aenter__ = AsyncMock(
            side_effect=TimeoutError(),
        )
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()

        assert result is False

    async def test_should_parse_json_response_on_success(self):
        """On 200, response.json() is called to get health data."""
        health_response = AsyncMock()
        health_response.status = 200
        health_response.json = AsyncMock(return_value={"status": "ok"})
        health_response.__aenter__ = AsyncMock(return_value=health_response)
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        result = await client.health_check()
        assert result is True
        health_response.json.assert_called_once()

    async def test_should_log_health_ok_on_success(self, caplog):
        """Health check success logs info with health data."""
        health_response = AsyncMock()
        health_response.status = 200
        health_response.json = AsyncMock(return_value={"status": "ok"})
        health_response.__aenter__ = AsyncMock(return_value=health_response)
        health_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(return_value=health_response)

        client = _make_client(session)

        with caplog.at_level(logging.INFO):
            await client.health_check()

        assert "Health OK" in caplog.text


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestGatewayClientSession:
    """Test session management."""

    async def test_should_create_session_when_none(self):
        client = GatewayClient()
        assert client._session is None

        await client._ensure_session()

        assert client._session is not None
        await client.close()

    async def test_should_create_session_when_closed(self):
        client = GatewayClient()
        closed_session = MagicMock()
        closed_session.closed = True
        client._session = closed_session

        await client._ensure_session()

        assert client._session is not closed_session
        await client.close()

    async def test_should_reuse_open_session(self):
        session = _make_mock_session()
        client = _make_client(session)

        await client._ensure_session()

        assert client._session is session

    async def test_should_close_session_and_set_none(self):
        session = _make_mock_session()
        client = _make_client(session)

        await client.close()

        session.close.assert_called_once()
        assert client._session is None

    async def test_should_noop_close_when_no_session(self):
        client = GatewayClient()
        await client.close()
        assert client._session is None

    async def test_should_noop_close_when_session_already_closed(self):
        session = MagicMock()
        session.closed = True
        client = _make_client(session)

        await client.close()

        session.close.assert_not_called()


# ---------------------------------------------------------------------------
# Reasoning content (DeepSeek reasoner)
# ---------------------------------------------------------------------------


class TestGatewayClientReasoningContent:
    """Test reasoning_content handling (DeepSeek reasoner)."""

    async def test_should_include_reasoning_content_when_present(self):
        session = _make_mock_session(
            response_json={
                "choices": [
                    {
                        "message": {
                            "content": "The answer is 42.",
                            "reasoning_content": "Let me think step by step...",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "deepseek-r1",
                "usage": {"total_tokens": 100},
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "What is the meaning?"}],
        )

        assert result is not None
        assert result["content"] == "The answer is 42."
        assert result["reasoning_content"] == "Let me think step by step..."

    async def test_should_omit_reasoning_content_when_absent(self):
        session = _make_mock_session(
            response_json={
                "choices": [
                    {
                        "message": {"content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        result = await client.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result is not None
        assert "reasoning_content" not in result

    async def test_should_log_reasoner_response_when_reasoning_present(self, caplog):
        """When reasoning_content is present, a specific log line is emitted."""
        session = _make_mock_session(
            response_json={
                "choices": [
                    {
                        "message": {
                            "content": "Answer",
                            "reasoning_content": "Thinking...",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "deepseek-r1",
                "usage": {"total_tokens": 100},
            }
        )
        client = _make_client(session)

        with caplog.at_level(logging.INFO):
            await client.chat(messages=[{"role": "user", "content": "Q"}])

        assert "REASONER" in caplog.text

    async def test_should_log_normal_response_when_no_reasoning(self, caplog):
        """When no reasoning_content, a standard response log line is emitted."""
        session = _make_mock_session(
            response_json={
                "choices": [
                    {
                        "message": {"content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "qwen3:8b",
                "usage": {"total_tokens": 10},
            }
        )
        client = _make_client(session)

        with caplog.at_level(logging.INFO):
            await client.chat(messages=[{"role": "user", "content": "Hi"}])

        assert "Response in" in caplog.text


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------


class TestGatewayClientEmbed:
    """Test the embed() method."""

    async def test_should_return_embedding_vector(self):
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        session = _make_mock_session(
            response_json={"embedding": embedding},
        )
        client = _make_client(session)

        result = await client.embed("Hello world")

        assert result == embedding
        url = session.post.call_args[0][0]
        assert "/v1/embeddings" in url
        assert "text=Hello" in url

    async def test_should_return_empty_list_for_empty_text(self):
        session = _make_mock_session()
        client = _make_client(session)

        result = await client.embed("")

        assert result == []
        session.post.assert_not_called()

    async def test_should_raise_on_http_error(self):
        session = _make_mock_session(response_status=500, response_text="Internal error")
        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="Embedding API error"):
            await client.embed("Hello")

    async def test_should_raise_on_embed_timeout(self):
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=TimeoutError())
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=mock_response)

        client = _make_client(session)

        with pytest.raises(LLMConnectionError, match="timed out"):
            await client.embed("Hello")

    async def test_should_raise_on_embed_connection_error(self):
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
            await client.embed("Hello")

    async def test_should_pass_custom_model_param(self):
        session = _make_mock_session(
            response_json={"embedding": [0.1]},
        )
        client = _make_client(session)

        await client.embed("Hello", model="custom-embed-model")

        url = session.post.call_args[0][0]
        assert "model=custom-embed-model" in url

    async def test_should_use_default_embed_model(self):
        """Default embed model is nomic-embed-text."""
        session = _make_mock_session(
            response_json={"embedding": [0.1]},
        )
        client = _make_client(session)

        await client.embed("Hello")

        url = session.post.call_args[0][0]
        assert "model=nomic-embed-text" in url

    async def test_should_use_30_second_timeout_for_embed(self):
        """Embed uses a 30-second timeout."""
        session = _make_mock_session(
            response_json={"embedding": [0.1]},
        )
        client = _make_client(session)

        await client.embed("Hello")

        call_kwargs = session.post.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 30

    async def test_should_truncate_embed_error_text_to_200_chars(self):
        """Error text is truncated at 200 chars."""
        long_error = "Q" * 500
        session = _make_mock_session(response_status=500, response_text=long_error)
        client = _make_client(session)

        with pytest.raises(LLMConnectionError) as exc_info:
            await client.embed("Hello")
        error_msg = str(exc_info.value)
        assert error_msg.count("Q") <= 200

    async def test_should_return_empty_list_when_embedding_key_missing(self):
        """When 'embedding' key is missing, return empty list."""
        session = _make_mock_session(
            response_json={"data": "something else"},
        )
        client = _make_client(session)

        result = await client.embed("Hello")
        assert result == []

    async def test_should_construct_url_with_urlencode(self):
        """URL uses urlencode for text and model params."""
        session = _make_mock_session(
            response_json={"embedding": [0.1]},
        )
        client = _make_client(session, base_url="http://myhost:8200")

        await client.embed("Hello World", model="nomic-embed-text")

        url = session.post.call_args[0][0]
        assert url.startswith("http://myhost:8200/v1/embeddings?")
        assert "text=Hello" in url


# ---------------------------------------------------------------------------
# LLMClient interface compliance
# ---------------------------------------------------------------------------


class TestGatewayClientInterface:
    """Verify GatewayClient implements the LLMClient abstract interface."""

    def test_should_be_subclass_of_llm_client(self):
        from overblick.core.llm.client import LLMClient

        assert issubclass(GatewayClient, LLMClient)

    def test_should_have_chat_method(self):
        assert callable(getattr(GatewayClient, "chat", None))

    def test_should_have_health_check_method(self):
        assert callable(getattr(GatewayClient, "health_check", None))

    def test_should_have_close_method(self):
        assert callable(getattr(GatewayClient, "close", None))
