"""
Tests for the provisioner — config/secret/directory creation.
"""

import yaml
import pytest
from pathlib import Path

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
        assert "config/overblick.yaml" in result["created_files"]

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

    def test_agent_overrides(self, tmp_path: Path, wizard_state: dict):
        provision(tmp_path, wizard_state)
        # Stål has non-default temperature (0.4 vs 0.7 default)
        override_path = tmp_path / "config" / "stal" / "config.yaml"
        assert override_path.exists()
        with open(override_path) as f:
            data = yaml.safe_load(f)
        assert data["llm"]["temperature"] == 0.4

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
