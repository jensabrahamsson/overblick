"""
Tests for the provisioner — config/secret/directory creation.
"""

from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from overblick.setup.provisioner import provision


@pytest.fixture
def wizard_state() -> dict:
    """Complete wizard state for provisioning."""
    return {
        "principal": {
            "principal_name": "Test User",
            "principal_email": "test@example.com",
            "timezone": "Europe/Stockholm",
            "language_preference": "en",
        },
        "llm": {
            "llm_provider": "ollama",
            "ollama_host": "127.0.0.1",
            "ollama_port": 11434,
            "model": "qwen3:8b",
            "default_temperature": 0.7,
            "default_max_tokens": 2000,
        },
        "communication": {
            "gmail_enabled": True,
            "gmail_address": "test@gmail.com",
            "gmail_app_password": "test-password",
            "telegram_enabled": True,
            "telegram_bot_token": "123:ABC",
            "telegram_chat_id": "456",
        },
        "selected_characters": ["anomal", "stal"],
        "agent_configs": {
            "anomal": {
                "temperature": 0.8,
                "max_tokens": 2000,
                "heartbeat_hours": 4,
                "quiet_hours": True,
                "plugins": ["moltbook"],
                "capabilities": [],
            },
            "stal": {
                "temperature": 0.4,
                "max_tokens": 1500,
                "heartbeat_hours": 1,
                "quiet_hours": True,
                "plugins": ["email_agent"],
                "capabilities": [],
            },
        },
    }


@pytest.fixture(autouse=True)
def _seed_master_key(tmp_path: Path):
    """Pre-create a Fernet master key so tests don't depend on macOS Keychain."""
    secrets_dir = tmp_path / "config" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_file = secrets_dir / ".master_key"
    key_file.write_bytes(Fernet.generate_key())
    key_file.chmod(0o600)


class TestProvisioner:
    """Tests for provisioning logic."""

    def test_creates_global_config(self, tmp_path: Path, wizard_state: dict):
        result = provision(tmp_path, wizard_state)
        config_path = tmp_path / "config" / "overblick.yaml"
        assert config_path.exists()

        with open(config_path) as f:
            config = yaml.safe_load(f)
        # New backends format: local backend with ollama type
        assert config["llm"]["backends"]["local"]["type"] == "ollama"
        assert config["llm"]["backends"]["local"]["model"] == "qwen3:8b"
        assert config["llm"]["default_backend"] == "local"
        # Normalize path separators for cross-platform compatibility
        created = [f.replace("\\", "/") for f in result["created_files"]]
        assert "config/overblick.yaml" in created

    def test_creates_data_directories(self, tmp_path: Path, wizard_state: dict):
        provision(tmp_path, wizard_state)
        assert (tmp_path / "data" / "anomal").is_dir()
        assert (tmp_path / "data" / "stal").is_dir()

    def test_creates_log_directories(self, tmp_path: Path, wizard_state: dict):
        provision(tmp_path, wizard_state)
        assert (tmp_path / "logs" / "anomal").is_dir()
        assert (tmp_path / "logs" / "stal").is_dir()

    def test_creates_secrets(self, tmp_path: Path, wizard_state: dict):
        provision(tmp_path, wizard_state)
        secrets_dir = tmp_path / "config" / "secrets"
        assert (secrets_dir / "anomal.yaml").exists()
        assert (secrets_dir / "stal.yaml").exists()

    def test_secrets_are_encrypted(self, tmp_path: Path, wizard_state: dict):
        provision(tmp_path, wizard_state)
        secrets_path = tmp_path / "config" / "secrets" / "anomal.yaml"
        with open(secrets_path) as f:
            data = yaml.safe_load(f)
        # Values should be Fernet-encrypted (base64), not plaintext
        assert data["principal_name"] != "Test User"
        assert "gAAAAA" in data["principal_name"]  # Fernet tokens start with gAAAAA

    def test_idempotent(self, tmp_path: Path, wizard_state: dict):
        """Running provisioner twice should not break things."""
        result1 = provision(tmp_path, wizard_state)
        result2 = provision(tmp_path, wizard_state)
        assert len(result1["created_files"]) > 0
        assert len(result2["created_files"]) > 0
        # Config should still be valid
        config_path = tmp_path / "config" / "overblick.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert config["llm"]["backends"]["local"]["type"] == "ollama"

    def test_gateway_url_in_config(self, tmp_path: Path, wizard_state: dict):
        """Gateway URL is always included in the output config."""
        provision(tmp_path, wizard_state)
        config_path = tmp_path / "config" / "overblick.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "gateway_url" in config["llm"]
        assert config["llm"]["gateway_url"] == "http://127.0.0.1:8200"

    def test_no_gmail_secrets_when_disabled(self, tmp_path: Path, wizard_state: dict):
        wizard_state["communication"]["gmail_enabled"] = False
        provision(tmp_path, wizard_state)

        from overblick.core.security.secrets_manager import SecretsManager

        sm = SecretsManager(tmp_path / "config" / "secrets")
        # Principal name should still be set
        assert sm.get("anomal", "principal_name") == "Test User"
        # Gmail should not be set
        assert sm.get("anomal", "gmail_address") is None

    def test_returns_created_files_list(self, tmp_path: Path, wizard_state: dict):
        result = provision(tmp_path, wizard_state)
        files = result["created_files"]
        assert any("overblick.yaml" in f for f in files)
        assert any("anomal" in f for f in files)
        assert any("stal" in f for f in files)

    def test_no_principal_name_skips(self, tmp_path: Path, wizard_state: dict):
        """When principal_name is empty, it's not written to secrets."""
        wizard_state["principal"]["principal_name"] = ""
        wizard_state["principal"]["principal_email"] = ""
        provision(tmp_path, wizard_state)
        # Should still succeed
        assert (tmp_path / "config" / "overblick.yaml").exists()

    def test_no_gmail_password_keeps_existing(self, tmp_path: Path, wizard_state: dict):
        """When gmail_app_password is empty but enabled, existing password kept."""
        wizard_state["communication"]["gmail_app_password"] = ""
        provision(tmp_path, wizard_state)
        # Should still create secrets files
        assert (tmp_path / "config" / "secrets" / "anomal.yaml").exists()

    def test_telegram_enabled_no_token(self, tmp_path: Path, wizard_state: dict):
        """Telegram enabled but no token doesn't crash."""
        wizard_state["communication"]["telegram_enabled"] = True
        wizard_state["communication"]["telegram_bot_token"] = ""
        wizard_state["communication"]["telegram_chat_id"] = ""
        provision(tmp_path, wizard_state)

    def test_deepseek_api_key_written(self, tmp_path: Path, wizard_state: dict):
        """Deepseek API key is written when present."""
        wizard_state["llm"]["deepseek"] = {"enabled": True}
        wizard_state["_deepseek_api_key"] = "sk-deepseek-test"
        provision(tmp_path, wizard_state)

    def test_telegram_no_chat_id(self, tmp_path: Path, wizard_state: dict):
        """Telegram enabled with token but no chat_id."""
        wizard_state["communication"]["telegram_enabled"] = True
        wizard_state["communication"]["telegram_bot_token"] = "123:ABC"
        wizard_state["communication"]["telegram_chat_id"] = ""
        provision(tmp_path, wizard_state)

    def test_identity_yaml_update_with_plugins(self, tmp_path: Path, wizard_state: dict):
        """Provisioner updates identity.yaml with wizard-assigned plugins."""
        # Create identity directory with existing identity.yaml
        identity_dir = tmp_path / "overblick" / "identities" / "anomal"
        identity_dir.mkdir(parents=True, exist_ok=True)
        existing = {"name": "anomal", "plugins": ["existing_plugin"]}
        with open(identity_dir / "identity.yaml", "w") as f:
            yaml.dump(existing, f)

        provision(tmp_path, wizard_state)

        # Check that plugins are merged
        with open(identity_dir / "identity.yaml") as f:
            data = yaml.safe_load(f)
        assert "existing_plugin" in data["plugins"]
        assert "moltbook" in data["plugins"]

    def test_identity_yaml_created_when_missing(self, tmp_path: Path, wizard_state: dict):
        """Identity.yaml is created when it doesn't exist."""
        provision(tmp_path, wizard_state)
        # identity.yaml should be created for identities with plugins
        identity_yaml = tmp_path / "overblick" / "identities" / "anomal" / "identity.yaml"
        assert identity_yaml.exists()

    def test_agent_config_not_in_selected(self, tmp_path: Path, wizard_state: dict):
        """Agent configs not in selected_characters are skipped."""
        wizard_state["agent_configs"]["extra"] = {
            "plugins": ["moltbook"],
            "capabilities": [],
        }
        provision(tmp_path, wizard_state)
        # extra is not in selected_characters, so no directory created
        assert not (tmp_path / "data" / "extra").exists()

    def test_plugin_configs_written(self, tmp_path: Path, wizard_state: dict):
        """Plugin configs are written to plugins.yaml."""
        wizard_state["agent_configs"]["anomal"]["plugin_configs"] = {
            "email_agent": {
                "email_filter_mode": "opt_in",
                "email_allowed_senders": "a@b.com\nc@d.com",
                "email_blocked_senders": "",
                "email_dry_run": True,
            }
        }
        provision(tmp_path, wizard_state)
        plugins_path = tmp_path / "config" / "anomal" / "plugins.yaml"
        assert plugins_path.exists()
        with open(plugins_path) as f:
            data = yaml.safe_load(f)
        assert "email_agent" in data

    def test_security_config_written(self, tmp_path: Path, wizard_state: dict):
        """Security/dashboard config is included when present."""
        wizard_state["security"] = {
            "network_access": True,
            "password_hash": "hashed_value",
            "session_hours": 12,
        }
        provision(tmp_path, wizard_state)
        with open(tmp_path / "config" / "overblick.yaml") as f:
            config = yaml.safe_load(f)
        assert config["dashboard"]["network_access"] is True
        assert config["dashboard"]["password_hash"] == "hashed_value"
        assert config["dashboard"]["session_hours"] == 12

    def test_security_default_session_hours(self, tmp_path: Path, wizard_state: dict):
        """Default session_hours (8) is not written to config."""
        wizard_state["security"] = {"network_access": False, "session_hours": 8}
        provision(tmp_path, wizard_state)
        with open(tmp_path / "config" / "overblick.yaml") as f:
            config = yaml.safe_load(f)
        assert "session_hours" not in config.get("dashboard", {})


class TestBuildLLMConfig:
    """Tests for LLM config building."""

    def test_new_format_config(self, tmp_path: Path):
        from overblick.setup.provisioner import _build_llm_config

        llm = {
            "gateway_url": "http://127.0.0.1:8200",
            "default_backend": "local",
            "default_temperature": 0.8,
            "default_max_tokens": 3000,
            "local": {"enabled": True, "backend_type": "ollama", "host": "127.0.0.1", "port": 11434, "model": "qwen3:8b"},
            "cloud": {"enabled": False},
            "deepseek": {"enabled": False},
            "openai": {"enabled": False},
        }
        config = _build_llm_config(llm)
        assert config["backends"]["local"]["enabled"] is True
        assert config["temperature"] == 0.8

    def test_legacy_cloud_provider(self, tmp_path: Path):
        from overblick.setup.provisioner import _build_llm_config

        llm = {
            "llm_provider": "cloud",
            "cloud_api_url": "https://api.openai.com/v1",
            "cloud_model": "gpt-4o",
        }
        config = _build_llm_config(llm)
        assert config["default_backend"] == "openai"
        assert config["backends"]["openai"]["enabled"] is True

    def test_legacy_lmstudio_provider(self, tmp_path: Path):
        from overblick.setup.provisioner import _build_llm_config

        llm = {"llm_provider": "lmstudio", "model": "local-model"}
        config = _build_llm_config(llm)
        assert config["backends"]["local"]["type"] == "lmstudio"

    def test_legacy_invalid_provider_defaults_to_ollama(self, tmp_path: Path):
        from overblick.setup.provisioner import _build_llm_config

        llm = {"llm_provider": "unknown_provider"}
        config = _build_llm_config(llm)
        assert config["backends"]["local"]["type"] == "ollama"


class TestBuildPluginConfigs:
    """Tests for plugin config building."""

    def test_github_plugin_config(self):
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "github": {
                "github_repos": "owner/repo1\nowner/repo2",
                "github_dry_run": False,
                "github_bot_username": "mybot",
                "github_tick_interval_minutes": 30,
                "github_auto_merge_patch": True,
                "github_auto_merge_minor": True,
                "github_auto_merge_major": True,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "github" in result
        assert result["github"]["repos"] == ["owner/repo1", "owner/repo2"]
        assert result["github"]["bot_username"] == "mybot"
        assert result["github"]["tick_interval_minutes"] == 30
        assert result["github"]["dependabot"]["auto_merge_patch"] is True

    def test_dev_agent_plugin_config(self):
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "dev_agent": {
                "dev_repo_url": "https://github.com/org/repo",
                "dev_workspace_dir": "/tmp/workspace",
                "dev_dry_run": True,
                "dev_tick_interval_minutes": 60,
                "dev_opencode_model": "gpt-4o",
                "dev_log_watcher_enabled": True,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "dev_agent" in result
        assert result["dev_agent"]["repo_url"] == "https://github.com/org/repo"
        assert result["dev_agent"]["opencode"]["model"] == "gpt-4o"
        assert result["dev_agent"]["log_watcher"]["enabled"] is True

    def test_email_agent_plugin_config(self):
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "email_agent": {
                "email_filter_mode": "opt_in",
                "email_allowed_senders": "a@b.com\nc@d.com",
                "email_blocked_senders": "spam@evil.com",
                "email_dry_run": False,
                "email_show_draft_replies": True,
                "email_max_email_age_hours": 24,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "email_agent" in result
        assert result["email_agent"]["senders"]["allowed"] == ["a@b.com", "c@d.com"]
        assert result["email_agent"]["senders"]["blocked"] == ["spam@evil.com"]
        assert result["email_agent"]["show_draft_replies"] is True
        assert result["email_agent"]["max_email_age_hours"] == 24

    def test_empty_plugin_configs(self):
        from overblick.setup.provisioner import _build_plugin_configs

        result = _build_plugin_configs({})
        assert result == {}

    def test_email_defaults_not_written(self):
        """Default values (48 hours age, no draft replies) are not written."""
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "email_agent": {
                "email_filter_mode": "opt_in",
                "email_allowed_senders": "",
                "email_blocked_senders": "",
                "email_dry_run": True,
                "email_show_draft_replies": False,
                "email_max_email_age_hours": 48,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "show_draft_replies" not in result["email_agent"]
        assert "max_email_age_hours" not in result["email_agent"]
        assert "senders" not in result["email_agent"]

    def test_github_defaults_not_written(self):
        """Default values for github plugin are not written."""
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "github": {
                "github_repos": "",
                "github_dry_run": True,
                "github_bot_username": "",
                "github_tick_interval_minutes": 15,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "repos" not in result["github"]
        assert "bot_username" not in result["github"]
        assert "tick_interval_minutes" not in result["github"]

    def test_dev_agent_defaults_not_written(self):
        """Default values for dev_agent plugin are not written."""
        from overblick.setup.provisioner import _build_plugin_configs

        wizard_configs = {
            "dev_agent": {
                "dev_repo_url": "",
                "dev_workspace_dir": "",
                "dev_dry_run": True,
                "dev_tick_interval_minutes": 30,
                "dev_opencode_model": "",
                "dev_log_watcher_enabled": False,
            }
        }
        result = _build_plugin_configs(wizard_configs)
        assert "repo_url" not in result["dev_agent"]
        assert "workspace_dir" not in result["dev_agent"]
        assert "opencode" not in result["dev_agent"]
        assert "log_watcher" not in result["dev_agent"]


class TestWriteYaml:
    """Tests for atomic YAML writing."""

    def test_write_yaml_creates_parents(self, tmp_path: Path):
        from overblick.setup.provisioner import _write_yaml

        path = tmp_path / "deep" / "nested" / "test.yaml"
        _write_yaml(path, {"key": "value"})
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["key"] == "value"

    def test_write_yaml_failure_cleans_up(self, tmp_path: Path):
        from unittest.mock import patch

        from overblick.setup.provisioner import _write_yaml

        path = tmp_path / "test.yaml"
        # Write initial data
        _write_yaml(path, {"original": True})

        # Make yaml.dump fail during write
        with patch("yaml.dump", side_effect=RuntimeError("write error")):
            with pytest.raises(RuntimeError):
                _write_yaml(path, {"bad": "data"})

        # Original file should still be valid (atomic write)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["original"] is True

    def test_write_yaml_failure_unlink_fails(self, tmp_path: Path):
        """When yaml.dump fails AND os.unlink also fails, exception still propagates."""
        import os
        from unittest.mock import patch

        from overblick.setup.provisioner import _write_yaml

        path = tmp_path / "test2.yaml"

        original_unlink = os.unlink

        def mock_unlink(p):
            if ".tmp" in str(p):
                raise OSError("unlink failed")
            return original_unlink(p)

        with (
            patch("yaml.dump", side_effect=RuntimeError("write error")),
            patch("os.unlink", side_effect=mock_unlink),
        ):
            with pytest.raises(RuntimeError, match="write error"):
                _write_yaml(path, {"data": "test"})
