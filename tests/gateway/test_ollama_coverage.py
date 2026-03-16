"""Additional tests for ollama_client — cover lines 87-91, 184-186, 219, 224-230."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from overblick.gateway.config import GatewayConfig
from overblick.gateway.models import ChatMessage, ChatRequest
from overblick.gateway.ollama_client import OllamaClient, OllamaConnectionError, OllamaError


@pytest.fixture
def config():
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        request_timeout_seconds=30.0,
    )


@pytest.fixture
def client(config):
    return OllamaClient(config)


class TestOllamaCoverage:
    @pytest.mark.asyncio
    async def test_list_models_generic_exception(self, client):
        """Cover lines 89-91: list_models returns [] on generic exception."""
        with MagicMock() as mock_get:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=RuntimeError("Unexpected"))
            with MagicMock() as _:
                client._client = mock_http
                client._client.is_closed = False

                models = await client.list_models()
                assert models == []

    @pytest.mark.asyncio
    async def test_chat_completion_generic_exception(self, client):
        """Cover lines 184-186: generic exception in chat_completion."""
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=ValueError("Unexpected"))
        mock_http.is_closed = False
        client._client = mock_http

        request = ChatRequest(
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        with pytest.raises(OllamaError, match="Failed to call"):
            await client.chat_completion(request)

    @pytest.mark.asyncio
    async def test_embed_returns_empty_for_no_embeddings(self, client):
        """Cover line 219: embeddings list is empty."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": []}
        mock_response.raise_for_status = MagicMock()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.embed("text")
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_http_status_error(self, client):
        """Cover lines 224-226: HTTP status error in embed."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        error = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=error)
        mock_http.is_closed = False
        client._client = mock_http

        with pytest.raises(OllamaError, match="Embedding request failed"):
            await client.embed("text")

    @pytest.mark.asyncio
    async def test_embed_generic_exception(self, client):
        """Cover lines 229-230: generic exception in embed."""
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=ValueError("Unexpected"))
        mock_http.is_closed = False
        client._client = mock_http

        with pytest.raises(OllamaError, match="Failed to generate embedding"):
            await client.embed("text")

    @pytest.mark.asyncio
    async def test_list_models_connect_error(self, client):
        """Cover lines 87-88: ConnectError in list_models."""
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_http.is_closed = False
        client._client = mock_http

        with pytest.raises(OllamaConnectionError):
            await client.list_models()
