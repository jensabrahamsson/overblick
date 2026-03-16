"""Tests for dashboard secrets service."""

from unittest.mock import MagicMock, patch

import pytest
import yaml

from overblick.dashboard.services.secrets import SecretsService


class TestSecretsServiceHasSecret:
    @pytest.fixture
    def svc(self, tmp_path):
        secrets_dir = tmp_path / "config" / "secrets"
        secrets_dir.mkdir(parents=True)
        return SecretsService(tmp_path)

    def test_should_return_true_when_secret_exists(self, svc, tmp_path):
        secrets_file = tmp_path / "config" / "secrets" / "anomal.yaml"
        secrets_file.write_text(yaml.safe_dump({"api_key": "encrypted_value"}))

        assert svc.has_secret("anomal", "api_key") is True

    def test_should_return_false_when_key_missing(self, svc, tmp_path):
        secrets_file = tmp_path / "config" / "secrets" / "anomal.yaml"
        secrets_file.write_text(yaml.safe_dump({"api_key": "encrypted_value"}))

        assert svc.has_secret("anomal", "nonexistent") is False

    def test_should_return_false_when_file_missing(self, svc):
        assert svc.has_secret("nonexistent", "key") is False

    def test_should_return_false_when_yaml_parse_error(self, svc, tmp_path):
        secrets_file = tmp_path / "config" / "secrets" / "broken.yaml"
        secrets_file.write_text(": invalid: yaml: [")

        assert svc.has_secret("broken", "key") is False

    def test_should_return_false_when_yaml_is_empty(self, svc, tmp_path):
        secrets_file = tmp_path / "config" / "secrets" / "empty.yaml"
        secrets_file.write_text("")

        assert svc.has_secret("empty", "key") is False


class TestSecretsServiceGetReadableSecret:
    @pytest.fixture
    def svc(self, tmp_path):
        secrets_dir = tmp_path / "config" / "secrets"
        secrets_dir.mkdir(parents=True)
        return SecretsService(tmp_path)

    def test_should_return_value_for_safe_key(self, svc):
        mock_sm = MagicMock()
        mock_sm.get.return_value = "user@example.com"

        with patch("overblick.core.security.secrets_manager.SecretsManager", return_value=mock_sm):
            result = svc.get_readable_secret("anomal", "gmail_address")

        assert result == "user@example.com"

    def test_should_return_value_for_all_safe_keys(self, svc):
        safe_keys = ["gmail_address", "telegram_chat_id", "principal_name", "principal_email"]
        mock_sm = MagicMock()
        mock_sm.get.return_value = "value"

        for key in safe_keys:
            with patch("overblick.core.security.secrets_manager.SecretsManager", return_value=mock_sm):
                result = svc.get_readable_secret("anomal", key)
            assert result == "value"

    def test_should_return_none_for_sensitive_key(self, svc):
        result = svc.get_readable_secret("anomal", "api_token")
        assert result is None

    def test_should_return_none_for_password_key(self, svc):
        result = svc.get_readable_secret("anomal", "password")
        assert result is None

    def test_should_return_none_when_secrets_manager_fails(self, svc):
        with patch(
            "overblick.core.security.secrets_manager.SecretsManager",
            side_effect=RuntimeError("decrypt failed"),
        ):
            result = svc.get_readable_secret("anomal", "gmail_address")
        assert result is None


class TestSecretsServiceListIdentitiesWithSecrets:
    def test_should_list_identities_with_yaml_files(self, tmp_path):
        secrets_dir = tmp_path / "config" / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "anomal.yaml").write_text("key: val")
        (secrets_dir / "cherry.yaml").write_text("key: val")
        (secrets_dir / ".hidden.yaml").write_text("key: val")
        (secrets_dir / "readme.txt").write_text("not yaml")

        svc = SecretsService(tmp_path)
        result = svc.list_identities_with_secrets()

        assert "anomal" in result
        assert "cherry" in result
        assert ".hidden" not in result
        assert "readme" not in result

    def test_should_return_empty_when_dir_missing(self, tmp_path):
        svc = SecretsService(tmp_path)
        result = svc.list_identities_with_secrets()
        assert result == []
