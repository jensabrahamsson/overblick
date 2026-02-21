"""
Integration tests — Gateway multi-backend routing pipeline.

Wires up real BackendRegistry + RequestRouter + QueueManager with
mock HTTP backends to verify the full routing pipeline.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.gateway.config import GatewayConfig, reset_config
from overblick.gateway.backend_registry import BackendRegistry
from overblick.gateway.router import RequestRouter
from overblick.gateway.models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatResponseChoice,
    ChatResponseUsage,
)


def _make_response(content: str = "Hello!", model: str = "qwen3:8b") -> ChatResponse:
    """Create a minimal ChatResponse."""
    return ChatResponse(
        id="test-123",
        model=model,
        choices=[ChatResponseChoice(message=ChatMessage(role="assistant", content=content))],
        usage=ChatResponseUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture
def multi_config():
    reset_config()
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        default_model="qwen3:8b",
        default_backend="local",
        request_timeout_seconds=10.0,
        backends={
            "local": {
                "enabled": True,
                "type": "ollama",
                "host": "127.0.0.1",
                "port": 11434,
                "model": "qwen3:8b",
            },
            "deepseek": {
                "enabled": True,
                "type": "deepseek",
                "api_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model": "deepseek-chat",
            },
        },
    )


class TestRoutingPipeline:
    """Full pipeline: Request → Router → Registry → Client."""

    def test_router_selects_local_by_default(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        assert router.resolve_backend() == "local"

    def test_router_selects_deepseek_for_high_complexity(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        assert router.resolve_backend(complexity="high") == "deepseek"

    def test_registry_provides_correct_client_after_routing(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        backend = router.resolve_backend(complexity="high")
        client = registry.get_client(backend)
        assert client is not None

    def test_model_matches_backend(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        backend = router.resolve_backend(complexity="high")
        model = registry.get_model(backend)
        assert model == "deepseek-chat"

    def test_fallback_when_preferred_missing(self, multi_config):
        """If deepseek is removed, high complexity falls back to local."""
        multi_config.backends.pop("deepseek")
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        assert router.resolve_backend(complexity="high") == "local"


class TestHealthCheckIntegration:
    """Health checks across multiple backends."""

    @pytest.mark.asyncio
    async def test_mixed_health(self, multi_config):
        registry = BackendRegistry(multi_config)
        # Mock clients
        local_mock = AsyncMock()
        local_mock.health_check.return_value = True
        local_mock.close.return_value = None
        deep_mock = AsyncMock()
        deep_mock.health_check.return_value = False
        deep_mock.close.return_value = None
        registry._clients["local"] = local_mock
        registry._clients["deepseek"] = deep_mock

        health = await registry.health_check_all()
        assert health["local"] is True
        assert health["deepseek"] is False
        await registry.close_all()

    @pytest.mark.asyncio
    async def test_all_healthy(self, multi_config):
        registry = BackendRegistry(multi_config)
        for name in registry.available_backends:
            mock = AsyncMock()
            mock.health_check.return_value = True
            mock.close.return_value = None
            registry._clients[name] = mock

        health = await registry.health_check_all()
        assert all(health.values())
        await registry.close_all()

    @pytest.mark.asyncio
    async def test_graceful_close_all(self, multi_config):
        registry = BackendRegistry(multi_config)
        mocks = {}
        for name in registry.available_backends:
            m = AsyncMock()
            m.close.return_value = None
            registry._clients[name] = m
            mocks[name] = m

        await registry.close_all()
        for m in mocks.values():
            m.close.assert_called_once()


class TestExplicitBackendOverride:
    """Verify explicit backend parameter overrides all routing logic."""

    def test_explicit_deepseek_ignores_low_complexity(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        result = router.resolve_backend(
            complexity="low", explicit_backend="deepseek"
        )
        assert result == "deepseek"

    def test_explicit_local_ignores_high_complexity(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        result = router.resolve_backend(
            complexity="high", explicit_backend="local"
        )
        assert result == "local"

    def test_explicit_nonexistent_falls_back(self, multi_config):
        registry = BackendRegistry(multi_config)
        router = RequestRouter(registry)
        result = router.resolve_backend(explicit_backend="openai")
        assert result == "local"  # Falls back to default
