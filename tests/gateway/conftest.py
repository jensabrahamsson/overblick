"""Pytest fixtures for LLM Gateway tests."""

import asyncio
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from overblick.gateway.config import GatewayConfig, reset_config
from overblick.gateway.models import ChatRequest, ChatResponse, ChatMessage, Priority
from overblick.gateway.ollama_client import OllamaClient
from overblick.gateway.queue_manager import QueueManager


@pytest.fixture
def test_config() -> GatewayConfig:
    """Create test configuration."""
    reset_config()
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        default_model="qwen3:8b",
        max_queue_size=10,
        request_timeout_seconds=30.0,
        max_concurrent_requests=1,
        api_host="127.0.0.1",
        api_port=8200,
    )


@pytest.fixture
def sample_request() -> ChatRequest:
    """Create a sample chat request."""
    return ChatRequest(
        model="qwen3:8b",
        messages=[
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="Hello!"),
        ],
        max_tokens=100,
        temperature=0.7,
    )


@pytest.fixture
def sample_response() -> ChatResponse:
    """Create a sample chat response."""
    return ChatResponse.from_message(
        model="qwen3:8b",
        content="Hello! How can I help you today?",
        usage={"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
    )


@pytest.fixture
def mock_ollama_client(sample_response: ChatResponse) -> AsyncMock:
    """Create a mock Ollama client."""
    client = AsyncMock(spec=OllamaClient)
    client.health_check.return_value = True
    client.list_models.return_value = ["qwen3:8b"]
    client.chat_completion.return_value = sample_response
    client.close.return_value = None
    return client


@pytest_asyncio.fixture
async def queue_manager(
    test_config: GatewayConfig,
    mock_ollama_client: AsyncMock,
) -> AsyncGenerator[QueueManager, None]:
    """Create a queue manager with mocked client."""
    qm = QueueManager(config=test_config, client=mock_ollama_client)
    await qm.start()
    yield qm
    await qm.stop()
