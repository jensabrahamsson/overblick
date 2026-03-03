"""
Configuration for the Internet Gateway (secure reverse proxy).

Settings can be overridden via environment variables with OVERBLICK_INET_ prefix.
Also reads from config/overblick.yaml (internet_gateway section) if present.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str) -> str:
    """Get environment variable with OVERBLICK_INET_ prefix."""
    return os.getenv(f"OVERBLICK_INET_{key}", default)


def _get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with OVERBLICK_INET_ prefix."""
    return int(_get_env(key, str(default)))


def _get_env_float(key: str, default: float) -> float:
    """Get float environment variable with OVERBLICK_INET_ prefix."""
    return float(_get_env(key, str(default)))


def _get_env_list(key: str, default: str = "") -> list[str]:
    """Get comma-separated list environment variable with OVERBLICK_INET_ prefix."""
    raw = _get_env(key, default)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


class InternetGatewayConfig(BaseModel):
    """Internet Gateway configuration with environment variable overrides."""

    # Network binding
    host: str = "0.0.0.0"
    port: int = 8201

    # TLS
    tls_cert_path: str = ""
    tls_key_path: str = ""
    tls_auto_selfsigned: bool = True

    # Internal gateway connection
    internal_gateway_url: str = "http://127.0.0.1:8200"
    internal_api_key: str = Field(default="", repr=False)

    # IP filtering
    ip_allowlist: list[str] = []  # CIDR notation, empty = all allowed
    trusted_proxies: list[
        str
    ] = []  # CIDR notation for trusted proxy IPs (for X-Forwarded-For validation)

    # Rate limiting
    global_rpm: int = 60
    per_key_rpm: int = 30

    # Request limits
    max_request_bytes: int = 65_536  # 64KB
    max_tokens_cap: int = 4096

    # Timeouts
    request_timeout: float = 120.0

    # Auto-ban
    auto_ban_threshold: int = 10  # violations before ban
    auto_ban_window: int = 300  # seconds to track violations (5 min)
    auto_ban_duration: int = 3600  # ban duration in seconds (1 hour)

    # Data directory
    data_dir: str = ""

    @property
    def resolved_data_dir(self) -> Path:
        """Get the data directory, defaulting to data/internet_gateway/."""
        if self.data_dir:
            return Path(self.data_dir)
        return Path(__file__).parent.parent.parent / "data" / "internet_gateway"

    @property
    def tls_enabled(self) -> bool:
        """Whether TLS is enabled (either provided certs or auto self-signed)."""
        if self.tls_cert_path and self.tls_key_path:
            return True
        return self.tls_auto_selfsigned

    def validate_safety(self) -> None:
        """Refuse to start if plaintext on public interface or invalid config.

        Raises:
            RuntimeError: If TLS is disabled and host is not localhost.
            ValueError: If internal_gateway_url has invalid scheme.
        """
        if not self.tls_enabled and self.host != "127.0.0.1":
            raise RuntimeError(
                "SAFETY: Refusing to start Internet Gateway without TLS on "
                f"host={self.host}. Either provide TLS certificates, enable "
                "tls_auto_selfsigned, or bind to 127.0.0.1 for dev mode."
            )

        if not self.internal_gateway_url.startswith(("http://", "https://")):
            raise ValueError(
                f"internal_gateway_url must start with http:// or https://, "
                f"got: {self.internal_gateway_url!r}"
            )

    @classmethod
    def from_env(cls) -> "InternetGatewayConfig":
        """Create config from environment variables and optional YAML config."""
        # Start with env vars
        config = cls(
            host=_get_env("HOST", "0.0.0.0"),
            port=_get_env_int("PORT", 8201),
            tls_cert_path=_get_env("TLS_CERT_PATH", ""),
            tls_key_path=_get_env("TLS_KEY_PATH", ""),
            tls_auto_selfsigned=_get_env("TLS_AUTO_SELFSIGNED", "true").lower()
            == "true",
            internal_gateway_url=_get_env(
                "INTERNAL_GATEWAY_URL", "http://127.0.0.1:8200"
            ),
            internal_api_key=_get_env(
                "INTERNAL_API_KEY",
                os.getenv("OVERBLICK_GATEWAY_KEY", ""),
            ),
            ip_allowlist=_get_env_list("IP_ALLOWLIST"),
            trusted_proxies=_get_env_list("TRUSTED_PROXIES"),
            global_rpm=_get_env_int("GLOBAL_RPM", 60),
            per_key_rpm=_get_env_int("PER_KEY_RPM", 30),
            max_request_bytes=_get_env_int("MAX_REQUEST_BYTES", 65_536),
            max_tokens_cap=_get_env_int("MAX_TOKENS_CAP", 4096),
            request_timeout=_get_env_float("REQUEST_TIMEOUT", 120.0),
            auto_ban_threshold=_get_env_int("AUTO_BAN_THRESHOLD", 10),
            auto_ban_window=_get_env_int("AUTO_BAN_WINDOW", 300),
            auto_ban_duration=_get_env_int("AUTO_BAN_DURATION", 3600),
            data_dir=_get_env("DATA_DIR", ""),
        )

        # Overlay YAML config
        yaml_config = _load_yaml_config()
        if yaml_config:
            inet = yaml_config.get("internet_gateway", {})
            if inet:
                for field_name in cls.model_fields:
                    if field_name in inet:
                        setattr(config, field_name, inet[field_name])
                logger.info(
                    "Loaded internet_gateway config from overblick.yaml (%d keys)",
                    len(inet),
                )

        return config


def _load_yaml_config() -> dict[str, Any]:
    """Load overblick.yaml if it exists (searches common locations)."""
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
_config: Optional[InternetGatewayConfig] = None


def get_inet_config() -> InternetGatewayConfig:
    """Get or create the internet gateway configuration singleton."""
    global _config
    if _config is None:
        _config = InternetGatewayConfig.from_env()
    return _config


def reset_inet_config() -> None:
    """Reset config singleton (useful for testing)."""
    global _config
    _config = None
