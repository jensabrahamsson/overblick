"""
Configuration for the LLM Gateway.

Settings can be overridden via environment variables with OVERBLICK_GW_ prefix.
Also reads from config/overblick.yaml if present (backends section).
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str) -> str:
    """Get environment variable with OVERBLICK_GW_ prefix."""
    return os.getenv(f"OVERBLICK_GW_{key}", default)


def _get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with OVERBLICK_GW_ prefix."""
    return int(_get_env(key, str(default)))


def _get_env_float(key: str, default: float) -> float:
    """Get float environment variable with OVERBLICK_GW_ prefix."""
    return float(_get_env(key, str(default)))


class GatewayConfig(BaseModel):
    """Gateway configuration with environment variable overrides."""

    # Ollama connection (legacy single-backend, used as fallback)
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434

    # Default model
    default_model: str = "qwen3:8b"

    # Queue settings
    max_queue_size: int = 100
    request_timeout_seconds: float = 300.0

    # Worker settings
    max_concurrent_requests: int = 1

    # API settings
    api_host: str = "127.0.0.1"
    api_port: int = 8200

    # Authentication (excluded from repr to prevent accidental logging)
    api_key: str = Field(default="", repr=False)

    # Logging
    log_level: str = "INFO"

    # Multi-backend configuration
    default_backend: str = "local"
    backends: dict[str, dict[str, Any]] = {}

    @property
    def ollama_base_url(self) -> str:
        """Get the full Ollama base URL."""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def ollama_chat_url(self) -> str:
        """Get the Ollama chat completions URL."""
        return f"{self.ollama_base_url}/v1/chat/completions"

    @property
    def ollama_models_url(self) -> str:
        """Get the Ollama models list URL."""
        return f"{self.ollama_base_url}/v1/models"

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Create config from environment variables and optional YAML config."""
        config = cls(
            ollama_host=_get_env("OLLAMA_HOST", "127.0.0.1"),
            ollama_port=_get_env_int("OLLAMA_PORT", 11434),
            default_model=_get_env("DEFAULT_MODEL", "qwen3:8b"),
            max_queue_size=_get_env_int("MAX_QUEUE_SIZE", 100),
            request_timeout_seconds=_get_env_float("REQUEST_TIMEOUT", 300.0),
            max_concurrent_requests=_get_env_int("MAX_CONCURRENT", 1),
            api_key=_get_env("API_KEY", os.getenv("OVERBLICK_GATEWAY_KEY", "")),
            api_host=_get_env("API_HOST", "127.0.0.1"),
            api_port=_get_env_int("API_PORT", 8200),
            log_level=_get_env("LOG_LEVEL", "INFO"),
        )

        # Try to load backends from overblick.yaml
        yaml_config = _load_yaml_config()
        if yaml_config:
            llm = yaml_config.get("llm", {})
            backends = llm.get("backends", {})
            if backends:
                config.backends = backends
                config.default_backend = llm.get("default_backend", "local")
                config.default_model = llm.get(
                    "model",
                    backends.get("local", {}).get("model", config.default_model),
                )
                # Update ollama host/port from local backend for backward compat
                local = backends.get("local", {})
                if local.get("enabled"):
                    config.ollama_host = local.get("host", config.ollama_host)
                    config.ollama_port = local.get("port", config.ollama_port)

                logger.info(
                    "Loaded %d backend(s) from overblick.yaml (default: %s)",
                    len(backends), config.default_backend,
                )

        # Inject Deepseek backend from env if not already in YAML
        deepseek_key = os.getenv("OVERBLICK_DEEPSEEK_API_KEY", "")
        if deepseek_key and "deepseek" not in config.backends:
            config.backends["deepseek"] = {
                "enabled": True,
                "type": "deepseek",
                "api_url": "https://api.deepseek.com/v1",
                "api_key": deepseek_key,
                "model": "deepseek-chat",
            }
            logger.info("Deepseek backend injected from OVERBLICK_DEEPSEEK_API_KEY env var")

        return config


def _load_yaml_config() -> dict[str, Any]:
    """Load overblick.yaml if it exists (searches common locations)."""
    # Search paths in priority order
    search_paths = [
        Path(os.getenv("OVERBLICK_CONFIG", "")) / "config" / "overblick.yaml",
        Path.cwd() / "config" / "overblick.yaml",
        Path(__file__).parent.parent.parent / "config" / "overblick.yaml",
    ]

    for path in search_paths:
        if path.exists():
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                logger.debug("Loaded config from %s", path)
                return data
            except Exception as e:
                logger.warning("Failed to load %s: %s", path, e)

    return {}


# Singleton config instance
_config: Optional[GatewayConfig] = None


def get_config() -> GatewayConfig:
    """Get or create the gateway configuration singleton."""
    global _config
    if _config is None:
        _config = GatewayConfig.from_env()
    return _config


def reset_config() -> None:
    """Reset config singleton (useful for testing)."""
    global _config
    _config = None
