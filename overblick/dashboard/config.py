"""
Dashboard configuration with environment variable and YAML overrides.

Priority: environment variable > YAML config > default value.

Settings can be overridden via OVERBLICK_DASH_ prefix:
    OVERBLICK_DASH_PORT=9090
    OVERBLICK_DASH_PASSWORD=mysecret
    OVERBLICK_DASH_SESSION_HOURS=24

Or via config/overblick.yaml:
    dashboard:
      network_access: false
      password_hash: ""
      session_hours: 8
"""

import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _env(key: str, default: str) -> str:
    """Get environment variable with OVERBLICK_DASH_ prefix."""
    return os.getenv(f"OVERBLICK_DASH_{key}", default)


def _env_int(key: str, default: int) -> int:
    return int(_env(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(_env(key, str(default)))


def _load_yaml_dashboard_config() -> dict[str, Any]:
    """Load dashboard section from config/overblick.yaml if it exists."""
    candidates = [
        Path.cwd() / "config" / "overblick.yaml",
        Path(__file__).parent.parent.parent / "config" / "overblick.yaml",
    ]
    for cfg_path in candidates:
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    data = yaml.safe_load(f) or {}
                return data.get("dashboard", {})
            except Exception as e:
                logger.warning("Failed to load dashboard config from %s: %s", cfg_path, e)
    return {}


class DashboardConfig(BaseModel):
    """Dashboard configuration."""

    # Network
    host: str = "127.0.0.1"
    port: int = 8080
    network_access: bool = False

    # Authentication
    password: str = ""          # plaintext (env-var compat, legacy)
    password_hash: str = ""     # bcrypt hash (from YAML / wizard)
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

    @property
    def auth_enabled(self) -> bool:
        """Whether authentication is required (password or hash configured)."""
        return bool(self.password or self.password_hash)

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        """Create config from environment variables + YAML (env wins)."""
        yaml_cfg = _load_yaml_dashboard_config()

        secret_key = _env("SECRET_KEY", "")
        if not secret_key:
            secret_key = secrets.token_hex(32)

        # YAML values as defaults, env-vars override
        network_access = yaml_cfg.get("network_access", False)
        password_hash = yaml_cfg.get("password_hash", "")
        session_hours_yaml = yaml_cfg.get("session_hours", 8)

        return cls(
            port=_env_int("PORT", 8080),
            network_access=network_access,
            password=_env("PASSWORD", ""),
            password_hash=password_hash,
            secret_key=secret_key,
            session_hours=_env_int("SESSION_HOURS", session_hours_yaml),
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
