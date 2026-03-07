"""Tests for Internet Gateway configuration."""

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest

from overblick.gateway.inet_config import (
    InternetGatewayConfig,
    get_inet_config,
    reset_inet_config,
)


@pytest.fixture(autouse=True)
def _reset_config() -> Generator[None]:
    """Reset config singleton before each test."""
    reset_inet_config()
    yield
    reset_inet_config()


class TestInternetGatewayConfig:
    """Tests for InternetGatewayConfig."""

    def test_defaults(self):
        config = InternetGatewayConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8201
        assert config.tls_auto_selfsigned is True
        assert config.internal_gateway_url == "http://127.0.0.1:8200"
        assert config.global_rpm == 60
        assert config.per_key_rpm == 30
        assert config.max_request_bytes == 65_536
        assert config.max_tokens_cap == 4096
        assert config.request_timeout == 120.0
        assert config.auto_ban_threshold == 10
        assert config.auto_ban_duration == 3600
        assert config.ip_allowlist == []

    def test_tls_enabled_with_certs(self):
        config = InternetGatewayConfig(
            tls_cert_path="/path/to/cert.pem",
            tls_key_path="/path/to/key.pem",
            tls_auto_selfsigned=False,
        )
        assert config.tls_enabled is True

    def test_tls_enabled_with_selfsigned(self):
        config = InternetGatewayConfig(tls_auto_selfsigned=True)
        assert config.tls_enabled is True

    def test_tls_disabled(self):
        config = InternetGatewayConfig(
            tls_cert_path="",
            tls_key_path="",
            tls_auto_selfsigned=False,
        )
        assert config.tls_enabled is False

    def test_safety_guard_blocks_plaintext_on_public(self):
        config = InternetGatewayConfig(
            host="0.0.0.0",
            tls_auto_selfsigned=False,
            tls_cert_path="",
            tls_key_path="",
        )
        with pytest.raises(RuntimeError, match="SAFETY"):
            config.validate_safety()

    def test_safety_guard_allows_plaintext_on_localhost(self):
        config = InternetGatewayConfig(
            host="127.0.0.1",
            tls_auto_selfsigned=False,
            tls_cert_path="",
            tls_key_path="",
        )
        # Should not raise
        config.validate_safety()

    def test_safety_guard_allows_tls_on_public(self):
        config = InternetGatewayConfig(
            host="0.0.0.0",
            tls_auto_selfsigned=True,
        )
        config.validate_safety()

    def test_from_env_with_overrides(self):
        env = {
            "OVERBLICK_INET_PORT": "9999",
            "OVERBLICK_INET_GLOBAL_RPM": "120",
            "OVERBLICK_INET_MAX_TOKENS_CAP": "2048",
            "OVERBLICK_INET_HOST": "127.0.0.1",
        }
        with patch.dict(os.environ, env, clear=False):
            config = InternetGatewayConfig.from_env()
            assert config.port == 9999
            assert config.global_rpm == 120
            assert config.max_tokens_cap == 2048
            assert config.host == "127.0.0.1"

    def test_from_env_ip_allowlist(self):
        env = {"OVERBLICK_INET_IP_ALLOWLIST": "10.0.0.0/8, 192.168.1.0/24"}
        with patch.dict(os.environ, env, clear=False):
            config = InternetGatewayConfig.from_env()
            assert config.ip_allowlist == ["10.0.0.0/8", "192.168.1.0/24"]

    def test_internal_api_key_not_in_repr(self):
        config = InternetGatewayConfig(internal_api_key="secret-key-123")
        repr_str = repr(config)
        assert "secret-key-123" not in repr_str

    def test_singleton(self):
        c1 = get_inet_config()
        c2 = get_inet_config()
        assert c1 is c2

    def test_reset_singleton(self):
        c1 = get_inet_config()
        reset_inet_config()
        c2 = get_inet_config()
        assert c1 is not c2
