"""Additional tests for inet_config — cover lines 112, 150-153, 176-179."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from overblick.gateway.inet_config import (
    InternetGatewayConfig,
    _load_yaml_config,
    reset_inet_config,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_inet_config()
    yield
    reset_inet_config()


class TestInetConfigCoverage:
    def test_validate_safety_invalid_internal_url(self):
        """Cover line 112: internal_gateway_url with invalid scheme."""
        config = InternetGatewayConfig(
            host="127.0.0.1",
            tls_auto_selfsigned=False,
            internal_gateway_url="ftp://bad-scheme",
        )
        with pytest.raises(ValueError, match="must start with http"):
            config.validate_safety()

    def test_from_env_with_yaml_overlay(self, tmp_path):
        """Cover lines 150-153: YAML config overlays env vars."""
        yaml_data = {
            "internet_gateway": {
                "port": 9999,
                "global_rpm": 999,
            }
        }
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        yaml_file = config_dir / "overblick.yaml"
        yaml_file.write_text(yaml.dump(yaml_data))

        env = {"OVERBLICK_CONFIG": str(tmp_path)}
        with patch.dict(os.environ, env, clear=False):
            config = InternetGatewayConfig.from_env()
            assert config.port == 9999
            assert config.global_rpm == 999

    def test_load_yaml_config_exception(self, tmp_path):
        """Cover lines 176-179: YAML load fails gracefully."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        yaml_file = config_dir / "overblick.yaml"
        yaml_file.write_text("{{{{invalid yaml")

        env = {"OVERBLICK_CONFIG": str(tmp_path)}
        with patch.dict(os.environ, env, clear=False):
            result = _load_yaml_config()
            # Should return empty dict on parse error (falls through to next path)
            assert isinstance(result, dict)

    def test_resolved_data_dir_custom(self):
        """Cover resolved_data_dir with custom path."""
        config = InternetGatewayConfig(data_dir="/custom/path")
        assert config.resolved_data_dir == Path("/custom/path")

    def test_resolved_data_dir_default(self):
        """Cover resolved_data_dir without custom path."""
        config = InternetGatewayConfig()
        assert "internet_gateway" in str(config.resolved_data_dir)
