"""Tests for Ollama client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from overblick.gateway.config import GatewayConfig
from overblick.gateway.models import ChatRequest, ChatMessage
from overblick.gateway.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaConnectionError,
    OllamaTimeoutError,
)


class TestOllamaClient:
    """Tests for OllamaClient."""

    @pytest.fixture
    def config(self):
        return GatewayConfig(
            ollama_host="127.0.0.1",
            ollama_port=11434,
            request_timeout_seconds=30.0,
        )

    @pytest.fixture
    def client(self, config):
        return OllamaClient(config)

    @pytest.fixture
    def sample_request(self):
        return ChatRequest(
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Hello")],
        )

    async def test_health_check_success(self, client):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.health_check()

            assert result is True

    async def test_health_check_failure(self, client):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_get.return_value = mock_http

            result = await client.health_check()

            assert result is False

    async def test_list_models(self, client):
        mock_response_data = {
            "data": [
                {"id": "qwen3:8b"},
                {"id": "llama3:8b"},
            ]
        }

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            models = await client.list_models()

            assert models == ["qwen3:8b", "llama3:8b"]

    async def test_chat_completion_success(self, client, sample_request):
        mock_response_data = {
            "id": "chatcmpl-123",
            "model": "qwen3:8b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi there!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            response = await client.chat_completion(sample_request)

            assert response.model == "qwen3:8b"
            assert len(response.choices) == 1
            assert response.choices[0].message.content == "Hi there!"
            assert response.usage.total_tokens == 15

    async def test_chat_completion_connection_error(self, client, sample_request):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http

            with pytest.raises(OllamaConnectionError) as exc_info:
                await client.chat_completion(sample_request)

            assert "Cannot connect to Ollama" in str(exc_info.value)

    async def test_chat_completion_timeout(self, client, sample_request):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_get.return_value = mock_http

            with pytest.raises(OllamaTimeoutError) as exc_info:
                await client.chat_completion(sample_request)

            assert "timed out" in str(exc_info.value)

    async def test_chat_completion_http_error(self, client, sample_request):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            error = httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )
            mock_http.post = AsyncMock(side_effect=error)
            mock_get.return_value = mock_http

            with pytest.raises(OllamaError) as exc_info:
                await client.chat_completion(sample_request)

            assert "500" in str(exc_info.value)

    async def test_close(self, client):
        with patch("httpx.AsyncClient") as mock_class:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            mock_class.return_value = mock_instance

            await client._get_client()
            await client.close()

            mock_instance.aclose.assert_called_once()
