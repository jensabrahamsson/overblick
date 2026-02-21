"""
Dashboard configuration with environment variable overrides.

Settings can be overridden via OVERBLICK_DASH_ prefix:
    OVERBLICK_DASH_PORT=9090
    OVERBLICK_DASH_PASSWORD=mysecret
    OVERBLICK_DASH_SESSION_HOURS=24
"""

import os
import secrets
from typing import Optional

from pydantic import BaseModel


def _env(key: str, default: str) -> str:
    """Get environment variable with OVERBLICK_DASH_ prefix."""
    return os.getenv(f"OVERBLICK_DASH_{key}", default)


def _env_int(key: str, default: int) -> int:
    return int(_env(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(_env(key, str(default)))


class DashboardConfig(BaseModel):
    """Dashboard configuration."""

    # Network — localhost ONLY (security: hardcoded, never overridable)
    host: str = "127.0.0.1"
    port: int = 8080

    # Authentication
    password: str = ""
    secret_key: str = ""
    session_hours: int = 8

    # Rate limiting
    login_rate_limit: int = 5        # attempts per window
    login_rate_window: int = 900     # 15 minutes in seconds
    api_rate_limit: int = 60         # requests per minute
    api_rate_window: int = 60        # 1 minute in seconds

    # Polling interval for htmx (seconds)
    poll_interval: int = 5

    # Test mode — disables auth, uses deterministic secret key
    test_mode: bool = False

    # Paths (set at runtime from base_dir)
    base_dir: str = ""

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        """Create config from environment variables."""
        secret_key = _env("SECRET_KEY", "")
        if not secret_key:
            secret_key = secrets.token_hex(32)

        return cls(
            port=_env_int("PORT", 8080),
            password=_env("PASSWORD", ""),
            secret_key=secret_key,
            session_hours=_env_int("SESSION_HOURS", 8),
            login_rate_limit=_env_int("LOGIN_RATE_LIMIT", 5),
            login_rate_window=_env_int("LOGIN_RATE_WINDOW", 900),
            api_rate_limit=_env_int("API_RATE_LIMIT", 60),
            api_rate_window=_env_int("API_RATE_WINDOW", 60),
            poll_interval=_env_int("POLL_INTERVAL", 5),
        )


# Singleton
_config: Optional[DashboardConfig] = None


def get_config() -> DashboardConfig:
    """Get or create the dashboard configuration singleton."""
    global _config
    if _config is None:
        _config = DashboardConfig.from_env()
    return _config


def reset_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    _config = None
