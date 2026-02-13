"""
Configuration for the LLM Gateway.

Settings can be overridden via environment variables with OVERBLICK_GW_ prefix.
"""

import os
from typing import Optional

from pydantic import BaseModel


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

    # Ollama connection
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

    # Logging
    log_level: str = "INFO"

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
        """Create config from environment variables (OVERBLICK_GW_ prefix)."""
        return cls(
            ollama_host=_get_env("OLLAMA_HOST", "127.0.0.1"),
            ollama_port=_get_env_int("OLLAMA_PORT", 11434),
            default_model=_get_env("DEFAULT_MODEL", "qwen3:8b"),
            max_queue_size=_get_env_int("MAX_QUEUE_SIZE", 100),
            request_timeout_seconds=_get_env_float("REQUEST_TIMEOUT", 300.0),
            max_concurrent_requests=_get_env_int("MAX_CONCURRENT", 1),
            api_host=_get_env("API_HOST", "127.0.0.1"),
            api_port=_get_env_int("API_PORT", 8200),
            log_level=_get_env("LOG_LEVEL", "INFO"),
        )


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
