"""
Tests for BackendRegistry — multi-backend client lifecycle management.

Covers:
- Backend registration (ollama, lmstudio, deepseek, openai, unknown)
- Client retrieval and model lookup
- Health checking and graceful shutdown
- Legacy fallback when no backends configured
- Deepseek API key handling (present, missing, env var)
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from overblick.gateway.config import GatewayConfig
from overblick.gateway.backend_registry import BackendRegistry, BackendConfig


@pytest.fixture
def base_config() -> GatewayConfig:
    """Config with no backends (triggers legacy fallback)."""
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        default_model="qwen3:8b",
        request_timeout_seconds=30.0,
    )


@pytest.fixture
def multi_backend_config() -> GatewayConfig:
    """Config with local + deepseek backends."""
    return GatewayConfig(
        ollama_host="127.0.0.1",
        ollama_port=11434,
        default_model="qwen3:8b",
        default_backend="local",
        request_timeout_seconds=30.0,
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
                "api_key": "sk-test-key",
                "model": "deepseek-chat",
            },
        },
    )


@pytest.fixture
def disabled_backend_config() -> GatewayConfig:
    """Config with a disabled backend."""
    return GatewayConfig(
        default_backend="local",
        backends={
            "local": {
                "enabled": True,
                "type": "ollama",
                "host": "127.0.0.1",
                "port": 11434,
                "model": "qwen3:8b",
            },
            "cloud": {
                "enabled": False,
                "type": "deepseek",
            },
        },
    )


class TestBackendConfig:
    """Tests for BackendConfig dataclass."""

    def test_base_url_from_host_port(self):
        bc = BackendConfig(
            name="local", enabled=True, backend_type="ollama",
            host="127.0.0.1", port=11434,
        )
        assert bc.base_url == "http://127.0.0.1:11434"

    def test_base_url_from_api_url(self):
        bc = BackendConfig(
            name="deepseek", enabled=True, backend_type="deepseek",
            api_url="https://api.deepseek.com/v1",
        )
        assert bc.base_url == "https://api.deepseek.com/v1"

    def test_api_url_takes_precedence_over_host_port(self):
        bc = BackendConfig(
            name="test", enabled=True, backend_type="deepseek",
            host="127.0.0.1", port=8080,
            api_url="https://custom.api.com",
        )
        assert bc.base_url == "https://custom.api.com"


class TestRegistration:
    """Tests for backend registration during init."""

    def test_ollama_backend_registered(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        assert "local" in registry.available_backends

    def test_deepseek_backend_registered(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        assert "deepseek" in registry.available_backends

    def test_disabled_backend_skipped(self, disabled_backend_config):
        registry = BackendRegistry(disabled_backend_config)
        assert "cloud" not in registry.available_backends
        assert "local" in registry.available_backends

    def test_unknown_type_skipped(self):
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
                "weird": {"enabled": True, "type": "alien_backend"},
            },
        )
        registry = BackendRegistry(config)
        assert "weird" not in registry.available_backends
        assert "local" in registry.available_backends

    def test_openai_type_skipped_with_log(self):
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
                "openai": {"enabled": True, "type": "openai"},
            },
        )
        registry = BackendRegistry(config)
        assert "openai" not in registry.available_backends

    def test_lmstudio_registers_as_ollama_client(self):
        config = GatewayConfig(
            default_backend="lmstudio",
            backends={
                "lmstudio": {
                    "enabled": True,
                    "type": "lmstudio",
                    "host": "127.0.0.1",
                    "port": 1234,
                    "model": "local-model",
                },
            },
        )
        registry = BackendRegistry(config)
        assert "lmstudio" in registry.available_backends

    def test_deepseek_missing_api_key_still_registers(self):
        """Deepseek with no API key should still register (with warning)."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
                "deepseek": {
                    "enabled": True,
                    "type": "deepseek",
                    "api_url": "https://api.deepseek.com/v1",
                    # No api_key
                },
            },
        )
        with patch.dict(os.environ, {}, clear=False):
            # Ensure env var is not set
            os.environ.pop("OVERBLICK_DEEPSEEK_API_KEY", None)
            registry = BackendRegistry(config)
        assert "deepseek" in registry.available_backends

    def test_deepseek_api_key_from_env(self):
        """Deepseek picks up API key from env var."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "deepseek": {
                    "enabled": True,
                    "type": "deepseek",
                    "api_url": "https://api.deepseek.com/v1",
                    # No explicit api_key in config
                },
            },
        )
        with patch.dict(os.environ, {"OVERBLICK_DEEPSEEK_API_KEY": "sk-from-env"}):
            registry = BackendRegistry(config)
        assert "deepseek" in registry.available_backends


class TestLegacyFallback:
    """Tests for legacy single-backend fallback."""

    def test_no_backends_creates_default_local(self, base_config):
        """With empty backends dict, falls back to a default OllamaClient."""
        registry = BackendRegistry(base_config)
        assert "local" in registry.available_backends
        assert registry.default_backend == "local"

    def test_all_disabled_creates_fallback(self):
        config = GatewayConfig(
            backends={
                "cloud": {"enabled": False, "type": "deepseek"},
            },
        )
        registry = BackendRegistry(config)
        assert "local" in registry.available_backends


class TestClientRetrieval:
    """Tests for get_client() and get_model()."""

    def test_get_default_client(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        client = registry.get_client()
        assert client is not None

    def test_get_specific_client(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        client = registry.get_client("deepseek")
        assert client is not None

    def test_get_nonexistent_client_raises(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        with pytest.raises(ValueError, match="not configured"):
            registry.get_client("nonexistent")

    def test_get_model_for_backend(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        assert registry.get_model("local") == "qwen3:8b"
        assert registry.get_model("deepseek") == "deepseek-chat"

    def test_get_model_default(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        assert registry.get_model() == "qwen3:8b"

    def test_get_model_unknown_backend_returns_default(self, base_config):
        registry = BackendRegistry(base_config)
        # Fallback backend won't have a BackendConfig entry
        assert registry.get_model("local") == "qwen3:8b"


class TestProperties:
    """Tests for registry properties."""

    def test_default_backend(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        assert registry.default_backend == "local"

    def test_available_backends(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        backends = registry.available_backends
        assert isinstance(backends, list)
        assert "local" in backends
        assert "deepseek" in backends


class TestHealthAndClose:
    """Tests for health_check_all() and close_all()."""

    @pytest.mark.asyncio
    async def test_health_check_all(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        # Patch clients with mocks
        for name in registry.available_backends:
            mock_client = AsyncMock()
            mock_client.health_check.return_value = True
            registry._clients[name] = mock_client

        results = await registry.health_check_all()
        assert results["local"] is True
        assert results["deepseek"] is True

    @pytest.mark.asyncio
    async def test_health_check_mixed_results(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        mock_healthy = AsyncMock()
        mock_healthy.health_check.return_value = True
        mock_unhealthy = AsyncMock()
        mock_unhealthy.health_check.return_value = False

        registry._clients["local"] = mock_healthy
        registry._clients["deepseek"] = mock_unhealthy

        results = await registry.health_check_all()
        assert results["local"] is True
        assert results["deepseek"] is False

    @pytest.mark.asyncio
    async def test_close_all(self, multi_backend_config):
        registry = BackendRegistry(multi_backend_config)
        mocks = {}
        for name in registry.available_backends:
            mock_client = AsyncMock()
            registry._clients[name] = mock_client
            mocks[name] = mock_client

        await registry.close_all()

        for mock in mocks.values():
            mock.close.assert_called_once()
