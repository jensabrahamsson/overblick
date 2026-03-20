"""Pytest fixtures for LLM Gateway tests."""

import asyncio
import sys
from collections.abc import AsyncGenerator, Generator
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Stub the missing dashscope_client module before any gateway imports.
# The production code in backend_registry.py imports DashScopeClient from it,
# but the module has not been created yet.  A fake module with a MagicMock
# class lets the import succeed so tests can run.
if "overblick.gateway.dashscope_client" not in sys.modules:
    _ds_mod = ModuleType("overblick.gateway.dashscope_client")
    _ds_mod.DashScopeClient = MagicMock(name="DashScopeClient")  # type: ignore[attr-defined]
    sys.modules["overblick.gateway.dashscope_client"] = _ds_mod

from contextlib import asynccontextmanager

from overblick.gateway.config import GatewayConfig, reset_config
from overblick.gateway.models import ChatMessage, ChatRequest, ChatResponse, Priority
from overblick.gateway.ollama_client import OllamaClient
from overblick.gateway.queue_manager import QueueManager


@asynccontextmanager
async def noop_lifespan(app):
    """No-op lifespan for tests that mock globals directly."""
    yield


@pytest.fixture
def test_config() -> GatewayConfig:
    """Create test configuration."""
    reset_config()
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        default_model="qwen3.5:9b",
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
        model="qwen3.5:9b",
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
        model="qwen3.5:9b",
        content="Hello! How can I help you today?",
        usage={"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
    )


@pytest.fixture
def mock_ollama_client(sample_response: ChatResponse) -> AsyncMock:
    """Create a mock Ollama client."""
    client = AsyncMock(spec=OllamaClient)
    client.health_check.return_value = True
    client.list_models.return_value = ["qwen3.5:9b"]
    client.chat_completion.return_value = sample_response
    client.close.return_value = None
    return client


@pytest_asyncio.fixture
async def queue_manager(
    test_config: GatewayConfig,
    mock_ollama_client: AsyncMock,
) -> AsyncGenerator[QueueManager]:
    """Create a queue manager with mocked client."""
    qm = QueueManager(config=test_config, client=mock_ollama_client)
    await qm.start()
    yield qm
    await qm.stop()
