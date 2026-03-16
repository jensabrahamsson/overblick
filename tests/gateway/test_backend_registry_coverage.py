"""Additional tests for backend_registry — cover lines 196, 225-226."""

from unittest.mock import AsyncMock

import pytest

from overblick.gateway.backend_registry import BackendRegistry
from overblick.gateway.config import GatewayConfig


class TestBackendRegistryCoverage:
    def test_get_backend_info(self):
        """Cover line 196: get_backend_info returns metadata."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {
                    "enabled": True,
                    "type": "ollama",
                    "host": "127.0.0.1",
                    "port": 11434,
                    "model": "qwen3:8b",
                },
            },
        )
        registry = BackendRegistry(config)
        info = registry.get_backend_info()
        assert "local" in info
        assert info["local"]["type"] == "ollama"
        assert info["local"]["model"] == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_close_all_handles_exception(self):
        """Cover lines 225-226: close_all handles exception in one client."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
            },
        )
        registry = BackendRegistry(config)

        mock_client = AsyncMock()
        mock_client.close = AsyncMock(side_effect=RuntimeError("close failed"))
        registry._clients["local"] = mock_client

        # Should not raise
        await registry.close_all()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_all_handles_exception(self):
        """Cover health_check_all when a client raises exception."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
            },
        )
        registry = BackendRegistry(config)

        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(side_effect=RuntimeError("health failed"))
        registry._clients["local"] = mock_client

        results = await registry.health_check_all()
        assert results["local"] is False
