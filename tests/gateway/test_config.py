"""Tests for LLM Gateway configuration."""

import os
import pytest

from overblick.gateway.config import GatewayConfig, get_config, reset_config


class TestGatewayConfig:
    """Tests for GatewayConfig."""

    def setup_method(self):
        reset_config()
        for key in list(os.environ.keys()):
            if key.startswith("OVERBLICK_GW_"):
                del os.environ[key]

    def teardown_method(self):
        reset_config()
        for key in list(os.environ.keys()):
            if key.startswith("OVERBLICK_GW_"):
                del os.environ[key]

    def test_default_values(self):
        config = GatewayConfig()
        assert config.ollama_host == "127.0.0.1"
        assert config.ollama_port == 11434
        assert config.default_model == "qwen3:8b"
        assert config.max_queue_size == 100
        assert config.max_concurrent_requests == 1
        assert config.api_port == 8200

    def test_environment_override_string(self):
        os.environ["OVERBLICK_GW_OLLAMA_HOST"] = "192.168.1.100"
        os.environ["OVERBLICK_GW_DEFAULT_MODEL"] = "llama3:8b"

        config = GatewayConfig.from_env()

        assert config.ollama_host == "192.168.1.100"
        assert config.default_model == "llama3:8b"

    def test_environment_override_int(self):
        os.environ["OVERBLICK_GW_OLLAMA_PORT"] = "11435"
        os.environ["OVERBLICK_GW_API_PORT"] = "8300"

        config = GatewayConfig.from_env()

        assert config.ollama_port == 11435
        assert config.api_port == 8300

    def test_environment_override_float(self):
        os.environ["OVERBLICK_GW_REQUEST_TIMEOUT"] = "120.5"

        config = GatewayConfig.from_env()

        assert config.request_timeout_seconds == 120.5

    def test_ollama_urls(self):
        config = GatewayConfig()
        assert config.ollama_base_url == "http://127.0.0.1:11434"
        assert config.ollama_chat_url == "http://127.0.0.1:11434/v1/chat/completions"
        assert config.ollama_models_url == "http://127.0.0.1:11434/v1/models"

    def test_custom_urls(self):
        config = GatewayConfig(ollama_host="ollama.local", ollama_port=8080)
        assert config.ollama_base_url == "http://ollama.local:8080"


class TestConfigSingleton:
    """Tests for config singleton pattern."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_get_config_returns_same_instance(self):
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_reset_config(self):
        config1 = get_config()
        reset_config()
        config2 = get_config()
        assert config1 is not config2
