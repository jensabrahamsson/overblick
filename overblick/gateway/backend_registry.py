"""
Backend registry for managing multiple LLM inference backends.

The gateway routes requests to one of several configured backends
(local Ollama, cloud Ollama/LM Studio, OpenAI). Each backend gets
its own OllamaClient instance configured with the appropriate URL.
"""

import logging
from typing import Optional

from .config import GatewayConfig
from .ollama_client import OllamaClient

logger = logging.getLogger(__name__)


class BackendConfig:
    """Runtime config for a single backend."""

    def __init__(
        self,
        name: str,
        enabled: bool,
        backend_type: str,
        host: str,
        port: int,
        model: str,
    ):
        self.name = name
        self.enabled = enabled
        self.backend_type = backend_type
        self.host = host
        self.port = port
        self.model = model

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class BackendRegistry:
    """Manages multiple OllamaClient instances for different backends."""

    def __init__(self, config: GatewayConfig):
        self._clients: dict[str, OllamaClient] = {}
        self._backend_configs: dict[str, BackendConfig] = {}
        self._default = config.default_backend

        for name, bcfg in config.backends.items():
            if not bcfg.get("enabled", False):
                logger.debug("Backend '%s' disabled, skipping", name)
                continue

            bc = BackendConfig(
                name=name,
                enabled=True,
                backend_type=bcfg.get("type", "ollama"),
                host=bcfg.get("host", config.ollama_host),
                port=bcfg.get("port", config.ollama_port),
                model=bcfg.get("model", config.default_model),
            )
            self._backend_configs[name] = bc

            # Create a per-backend GatewayConfig copy for OllamaClient
            client_config = GatewayConfig(
                ollama_host=bc.host,
                ollama_port=bc.port,
                default_model=bc.model,
                request_timeout_seconds=config.request_timeout_seconds,
            )
            self._clients[name] = OllamaClient(client_config)
            logger.info(
                "Registered backend '%s': %s (model: %s)",
                name, bc.base_url, bc.model,
            )

        if not self._clients:
            # Fallback: create default client from legacy config
            logger.info("No backends configured, using legacy single-backend config")
            self._clients["local"] = OllamaClient(config)
            self._default = "local"

    def get_client(self, backend: Optional[str] = None) -> OllamaClient:
        """Get OllamaClient for a backend (defaults to default_backend)."""
        name = backend or self._default
        if name not in self._clients:
            raise ValueError(f"Backend '{name}' not configured or not enabled")
        return self._clients[name]

    def get_model(self, backend: Optional[str] = None) -> str:
        """Get default model for a backend."""
        name = backend or self._default
        bcfg = self._backend_configs.get(name)
        if bcfg:
            return bcfg.model
        return "qwen3:8b"

    @property
    def default_backend(self) -> str:
        return self._default

    @property
    def available_backends(self) -> list[str]:
        return list(self._clients.keys())

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all registered backends."""
        results = {}
        for name, client in self._clients.items():
            results[name] = await client.health_check()
        return results

    async def close_all(self) -> None:
        """Close all client connections."""
        for client in self._clients.values():
            await client.close()
