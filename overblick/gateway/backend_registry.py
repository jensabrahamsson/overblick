"""
Backend registry for managing multiple LLM inference backends.

The gateway routes requests to one of several configured backends
(local Ollama, cloud Ollama/LM Studio, Deepseek, OpenAI). Each backend
gets its own client instance configured with the appropriate URL/credentials.

Supported backend types:
- ollama / lmstudio → OllamaClient (OpenAI-compatible local inference)
- deepseek → DeepseekClient (OpenAI-compatible cloud API with Bearer auth)
- openai → logged as "coming soon", skipped
"""

import logging
import os
from typing import Any, Optional

from .config import GatewayConfig
from .deepseek_client import DeepseekClient
from .ollama_client import OllamaClient

logger = logging.getLogger(__name__)


class BackendConfig:
    """Runtime config for a single backend."""

    def __init__(
        self,
        name: str,
        enabled: bool,
        backend_type: str,
        host: str = "",
        port: int = 0,
        model: str = "",
        api_url: str = "",
        api_key: str = "",
    ):
        self.name = name
        self.enabled = enabled
        self.backend_type = backend_type
        self.host = host
        self.port = port
        self.model = model
        self.api_url = api_url
        self.api_key = api_key

    @property
    def base_url(self) -> str:
        if self.api_url:
            return self.api_url
        return f"http://{self.host}:{self.port}"


class BackendRegistry:
    """Manages multiple backend client instances for different backends."""

    def __init__(self, config: GatewayConfig):
        self._clients: dict[str, Any] = {}
        self._backend_configs: dict[str, BackendConfig] = {}
        self._default = config.default_backend

        for name, bcfg in config.backends.items():
            if not bcfg.get("enabled", False):
                logger.debug("Backend '%s' disabled, skipping", name)
                continue

            btype = bcfg.get("type", "ollama")

            if btype in ("ollama", "lmstudio"):
                self._register_ollama_backend(name, bcfg, config)
            elif btype == "deepseek":
                self._register_deepseek_backend(name, bcfg, config)
            elif btype == "openai":
                logger.info(
                    "Backend '%s': OpenAI support coming soon, skipping",
                    name,
                )
            else:
                logger.warning(
                    "Backend '%s': unknown type '%s', skipping", name, btype
                )

        if not self._clients:
            # Fallback: create default client from legacy config
            logger.info("No backends configured, using legacy single-backend config")
            self._clients["local"] = OllamaClient(config)
            self._default = "local"

    def _register_ollama_backend(
        self,
        name: str,
        bcfg: dict[str, Any],
        config: GatewayConfig,
    ) -> None:
        """Register an Ollama/LM Studio backend."""
        bc = BackendConfig(
            name=name,
            enabled=True,
            backend_type=bcfg.get("type", "ollama"),
            host=bcfg.get("host", config.ollama_host),
            port=bcfg.get("port", config.ollama_port),
            model=bcfg.get("model", config.default_model),
        )
        self._backend_configs[name] = bc

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

    def _register_deepseek_backend(
        self,
        name: str,
        bcfg: dict[str, Any],
        config: GatewayConfig,
    ) -> None:
        """Register a Deepseek API backend."""
        api_url = bcfg.get("api_url", "https://api.deepseek.com/v1")
        api_key = (
            bcfg.get("api_key")
            or os.getenv("OVERBLICK_DEEPSEEK_API_KEY", "")
        )
        model = bcfg.get("model", "deepseek-chat")

        if not api_key:
            logger.warning(
                "Backend '%s': Deepseek enabled but no API key configured "
                "(set api_key in config or OVERBLICK_DEEPSEEK_API_KEY env var)",
                name,
            )

        bc = BackendConfig(
            name=name,
            enabled=True,
            backend_type="deepseek",
            model=model,
            api_url=api_url,
            api_key=api_key,
        )
        self._backend_configs[name] = bc

        self._clients[name] = DeepseekClient(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_seconds=config.request_timeout_seconds,
        )
        logger.info(
            "Registered backend '%s': %s (model: %s, key: %s)",
            name, api_url, model, "configured" if api_key else "MISSING",
        )

    def get_client(self, backend: Optional[str] = None) -> Any:
        """Get client for a backend (defaults to default_backend)."""
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
