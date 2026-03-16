"""Tests for dashboard onboarding service."""

from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
import yaml

from overblick.dashboard.services.onboarding import OnboardingService


class TestOnboardingService:
    @pytest.fixture
    def svc(self, tmp_path):
        return OnboardingService(tmp_path)

    def test_should_init_with_correct_paths(self, tmp_path):
        svc = OnboardingService(tmp_path)
        assert svc._base_dir == tmp_path
        assert svc._identities_dir == tmp_path / "overblick" / "identities"
        assert svc._secrets_dir == tmp_path / "config" / "secrets"

    def test_should_return_false_when_identity_not_exists(self, svc, tmp_path):
        assert svc.identity_exists("nonexistent") is False

    def test_should_return_true_when_identity_exists(self, svc, tmp_path):
        identity_dir = tmp_path / "overblick" / "identities" / "testbot"
        identity_dir.mkdir(parents=True)
        (identity_dir / "identity.yaml").write_text("name: testbot")
        assert svc.identity_exists("testbot") is True

    def test_should_raise_when_name_empty(self, svc):
        with pytest.raises(ValueError, match="Identity name is required"):
            svc.create_identity({})

    def test_should_raise_when_name_missing(self, svc):
        with pytest.raises(ValueError, match="Identity name is required"):
            svc.create_identity({"name": ""})

    def test_should_raise_when_identity_already_exists(self, svc, tmp_path):
        identity_dir = tmp_path / "overblick" / "identities" / "existing"
        identity_dir.mkdir(parents=True)
        (identity_dir / "identity.yaml").write_text("name: existing")

        with pytest.raises(ValueError, match="already exists"):
            svc.create_identity({"name": "existing"})

    def test_should_create_identity_with_minimal_state(self, svc, tmp_path):
        result = svc.create_identity({"name": "testbot"})

        assert result["name"] == "testbot"
        assert len(result["created_files"]) == 1
        assert "identity.yaml" in result["created_files"][0]
        assert result["identity_dir"] == str(tmp_path / "overblick" / "identities" / "testbot")
        assert result["data_dir"] == str(tmp_path / "data" / "testbot")

        # Verify the yaml file was created
        yaml_path = tmp_path / "overblick" / "identities" / "testbot" / "identity.yaml"
        assert yaml_path.exists()
        config = yaml.safe_load(yaml_path.read_text())
        assert config["name"] == "testbot"
        assert config["display_name"] == "Testbot"

    def test_should_create_identity_with_full_state(self, svc, tmp_path):
        state = {
            "name": "fullbot",
            "display_name": "Full Bot",
            "description": "A full test bot",
            "llm": {
                "model": "llama3:8b",
                "temperature": 0.9,
                "max_tokens": 3000,
                "provider": "gateway",
            },
            "personality": "creative",
            "plugins": ["moltbook", "telegram"],
            "capabilities": ["social", "knowledge"],
        }
        result = svc.create_identity(state)
        assert result["name"] == "fullbot"

        yaml_path = tmp_path / "overblick" / "identities" / "fullbot" / "identity.yaml"
        config = yaml.safe_load(yaml_path.read_text())
        assert config["display_name"] == "Full Bot"
        assert config["description"] == "A full test bot"
        assert config["llm"]["model"] == "llama3:8b"
        assert config["personality"] == "creative"
        assert config["plugins"] == ["moltbook", "telegram"]
        assert config["capabilities"] == ["social", "knowledge"]
        assert config["schedule"]["heartbeat_hours"] == 4
        assert config["security"]["enable_preflight"] is True

    def test_should_create_identity_with_cloud_provider(self, svc, tmp_path):
        state = {
            "name": "cloudbot",
            "llm": {
                "provider": "cloud",
                "cloud_api_url": "https://api.example.com",
                "cloud_model": "gpt-4",
            },
        }
        result = svc.create_identity(state)

        yaml_path = tmp_path / "overblick" / "identities" / "cloudbot" / "identity.yaml"
        config = yaml.safe_load(yaml_path.read_text())
        assert config["llm"]["provider"] == "cloud"
        assert config["llm"]["cloud_api_url"] == "https://api.example.com"
        assert config["llm"]["cloud_model"] == "gpt-4"

    def test_should_store_secrets_when_provided(self, svc, tmp_path):
        mock_sm = MagicMock()
        state = {
            "name": "secretbot",
            "secrets": {"api_key": "secret123", "empty_key": ""},
        }

        with patch("overblick.core.security.secrets_manager.SecretsManager", return_value=mock_sm):
            result = svc.create_identity(state)

        # Only non-empty values are stored
        mock_sm.set.assert_called_once_with("secretbot", "api_key", "secret123")
        assert any("secrets/" in f for f in result["created_files"])

    def test_should_handle_secrets_storage_failure(self, svc, tmp_path):
        state = {
            "name": "failbot",
            "secrets": {"key": "value"},
        }

        with patch(
            "overblick.core.security.secrets_manager.SecretsManager",
            side_effect=RuntimeError("encryption failed"),
        ):
            result = svc.create_identity(state)

        # Should succeed even if secrets fail
        assert result["name"] == "failbot"
        assert len(result["created_files"]) == 1  # Only the yaml file

    def test_should_create_data_directory(self, svc, tmp_path):
        svc.create_identity({"name": "databot"})
        data_dir = tmp_path / "data" / "databot"
        assert data_dir.exists()


class TestOnboardingBuildIdentityConfig:
    @pytest.fixture
    def svc(self, tmp_path):
        return OnboardingService(tmp_path)

    def test_should_build_minimal_config(self, svc):
        config = svc._build_identity_config({"name": "minimal"})
        assert config["name"] == "minimal"
        assert config["display_name"] == "Minimal"
        assert config["description"] == ""
        assert config["version"] == "1.0.0"
        assert "llm" not in config
        assert "personality" not in config
        assert "plugins" not in config
        assert "capabilities" not in config

    def test_should_use_default_display_name(self, svc):
        config = svc._build_identity_config({"name": "mybot"})
        assert config["display_name"] == "Mybot"

    def test_should_use_custom_display_name(self, svc):
        config = svc._build_identity_config({"name": "mybot", "display_name": "My Bot"})
        assert config["display_name"] == "My Bot"

    def test_should_include_empty_llm_when_no_llm(self, svc):
        config = svc._build_identity_config({"name": "test", "llm": {}})
        # Empty dict is falsy, so no llm section
        assert "llm" not in config

    def test_should_not_include_empty_plugins(self, svc):
        config = svc._build_identity_config({"name": "test", "plugins": []})
        assert "plugins" not in config

    def test_should_not_include_empty_capabilities(self, svc):
        config = svc._build_identity_config({"name": "test", "capabilities": []})
        assert "capabilities" not in config

    def test_should_not_include_empty_personality(self, svc):
        config = svc._build_identity_config({"name": "test", "personality": ""})
        assert "personality" not in config


class TestOnboardingTestLLMConnection:
    @pytest.fixture
    def svc(self, tmp_path):
        return OnboardingService(tmp_path)

    def _make_mock_session_class(self, status=200):
        """Create a mock aiohttp.ClientSession that works with nested async with."""
        mock_resp = MagicMock()
        mock_resp.status = status

        class FakeRespCtx:
            async def __aenter__(self_inner):
                return mock_resp

            async def __aexit__(self_inner, *args):
                pass

        class FakeSession:
            def get(self_inner, *args, **kwargs):
                return FakeRespCtx()

        class FakeSessionCtx:
            async def __aenter__(self_inner):
                return FakeSession()

            async def __aexit__(self_inner, *args):
                pass

        return FakeSessionCtx

    def test_should_test_gateway_connection(self, svc):
        with patch("aiohttp.ClientSession", self._make_mock_session_class(200)):
            result = svc.test_llm_connection({"provider": "gateway", "model": "qwen3:8b"})

        assert result["success"] is True
        assert "qwen3:8b" in result["message"]

    def test_should_test_ollama_connection(self, svc):
        with patch("aiohttp.ClientSession", self._make_mock_session_class(200)):
            result = svc.test_llm_connection({"provider": "ollama", "model": "qwen3:8b"})
        assert result["success"] is True

    def test_should_test_cloud_connection(self, svc):
        with patch("aiohttp.ClientSession", self._make_mock_session_class(200)):
            result = svc.test_llm_connection({
                "provider": "cloud",
                "cloud_api_url": "https://api.example.com/v1",
                "model": "gpt-4",
            })
        assert result["success"] is True

    def test_should_fail_cloud_without_url(self, svc):
        result = svc.test_llm_connection({"provider": "cloud", "cloud_api_url": ""})
        assert result["success"] is False
        assert "Cloud API URL is required" in result["message"]

    def test_should_fail_when_non_200_status(self, svc):
        with patch("aiohttp.ClientSession", self._make_mock_session_class(500)):
            result = svc.test_llm_connection({"provider": "ollama"})
        assert result["success"] is False
        assert "non-200" in result["message"]

    def test_should_handle_connection_error(self, svc):
        with patch("aiohttp.ClientSession", side_effect=ConnectionError("refused")):
            result = svc.test_llm_connection({"provider": "ollama"})
        assert result["success"] is False
        assert "Connection failed" in result["message"]
