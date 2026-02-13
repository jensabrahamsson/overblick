"""Tests for dashboard configuration."""

import os
import pytest
from overblick.dashboard.config import DashboardConfig, get_config, reset_config


class TestDashboardConfig:
    def test_defaults(self):
        config = DashboardConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8080
        assert config.session_hours == 8
        assert config.login_rate_limit == 5
        assert config.api_rate_limit == 60

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("OVERBLICK_DASH_PORT", "9090")
        monkeypatch.setenv("OVERBLICK_DASH_PASSWORD", "secret123")
        monkeypatch.setenv("OVERBLICK_DASH_SESSION_HOURS", "24")

        config = DashboardConfig.from_env()
        assert config.port == 9090
        assert config.password == "secret123"
        assert config.session_hours == 24

    def test_from_env_generates_secret_key(self):
        config = DashboardConfig.from_env()
        assert config.secret_key
        assert len(config.secret_key) == 64  # hex(32 bytes)

    def test_from_env_uses_provided_secret_key(self, monkeypatch):
        monkeypatch.setenv("OVERBLICK_DASH_SECRET_KEY", "my-custom-key")
        config = DashboardConfig.from_env()
        assert config.secret_key == "my-custom-key"

    def test_singleton_pattern(self):
        reset_config()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reset_config(self):
        reset_config()
        c1 = get_config()
        reset_config()
        c2 = get_config()
        assert c1 is not c2
