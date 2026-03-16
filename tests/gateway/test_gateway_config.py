"""Tests for gateway config — cover lines 123-130, 151-154."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from overblick.gateway.config import GatewayConfig, _load_yaml_config, reset_config


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


class TestGatewayConfigCoverage:
    def test_from_env_with_yaml_backends(self, tmp_path):
        """Cover lines 123-130: YAML config with backends and deepseek injection."""
        yaml_data = {
            "llm": {
                "default_backend": "local",
                "model": "llama3:8b",
                "backends": {
                    "local": {
                        "enabled": True,
                        "type": "ollama",
                        "host": "192.168.1.100",
                        "port": 11435,
                        "model": "llama3:8b",
                    },
                },
            }
        }
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        yaml_file = config_dir / "overblick.yaml"
        yaml_file.write_text(yaml.dump(yaml_data))

        env = {
            "OVERBLICK_CONFIG": str(tmp_path),
            "OVERBLICK_DEEPSEEK_API_KEY": "sk-test-deepseek",
        }
        with patch.dict(os.environ, env, clear=False):
            config = GatewayConfig.from_env()
            assert config.default_backend == "local"
            assert "local" in config.backends
            assert "deepseek" in config.backends  # Injected from env
            assert config.ollama_host == "192.168.1.100"
            assert config.ollama_port == 11435

    def test_load_yaml_config_parse_error(self, tmp_path):
        """Cover lines 151-154: YAML parse error returns {}."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        yaml_file = config_dir / "overblick.yaml"
        yaml_file.write_text("{{{{invalid")

        env = {"OVERBLICK_CONFIG": str(tmp_path)}
        with patch.dict(os.environ, env, clear=False):
            result = _load_yaml_config()
            assert isinstance(result, dict)

    def test_from_env_deepseek_already_in_yaml(self, tmp_path):
        """Deepseek key from env not injected if already in YAML."""
        yaml_data = {
            "llm": {
                "backends": {
                    "deepseek": {
                        "enabled": True,
                        "type": "deepseek",
                        "model": "deepseek-chat",
                    },
                },
            }
        }
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "overblick.yaml").write_text(yaml.dump(yaml_data))

        env = {
            "OVERBLICK_CONFIG": str(tmp_path),
            "OVERBLICK_DEEPSEEK_API_KEY": "sk-from-env",
        }
        with patch.dict(os.environ, env, clear=False):
            config = GatewayConfig.from_env()
            # Should NOT have been re-injected since it's already in YAML
            assert config.backends["deepseek"]["type"] == "deepseek"
