"""Unit tests for PluginContext — the plugin isolation boundary."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from overblick.core.plugin_base import PluginContext


class TestPluginContextGetSecret:
    def test_get_secret_existing_key(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        ctx._secrets_getter = lambda key: {"api_key": "secret123"}.get(key)
        assert ctx.get_secret("api_key") == "secret123"

    def test_get_secret_missing_key(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        ctx._secrets_getter = lambda key: None
        assert ctx.get_secret("nonexistent") is None

    def test_get_secret_no_getter(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.get_secret("anything") is None


class TestPluginContextGetCapability:
    def test_get_capability_existing(self, tmp_path):
        mock_cap = MagicMock()
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            capabilities={"email": mock_cap},
        )
        assert ctx.get_capability("email") is mock_cap

    def test_get_capability_missing(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.get_capability("nonexistent") is None


class TestPluginContextIdentity:
    def test_load_identity(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        with patch("overblick.identities.load_identity") as mock_load:
            mock_identity = MagicMock()
            mock_load.return_value = mock_identity
            result = ctx.load_identity("cherry")
            mock_load.assert_called_once_with("cherry")
            assert result is mock_identity

    def test_build_system_prompt(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        mock_identity = MagicMock()
        with patch("overblick.identities.build_system_prompt") as mock_build:
            mock_build.return_value = "You are Cherry."
            result = ctx.build_system_prompt(mock_identity, platform="Telegram")
            mock_build.assert_called_once_with(
                mock_identity, platform="Telegram", model_slug=""
            )
            assert result == "You are Cherry."


class TestPluginContextInit:
    def test_creates_directories(self, tmp_path):
        data_dir = tmp_path / "data" / "nested"
        log_dir = tmp_path / "logs" / "nested"
        ctx = PluginContext(
            identity_name="test",
            data_dir=data_dir,
            log_dir=log_dir,
        )
        assert data_dir.exists()
        assert log_dir.exists()

    def test_stores_identity_name(self, tmp_path):
        ctx = PluginContext(
            identity_name="anomal",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.identity_name == "anomal"

    def test_default_capabilities_empty(self, tmp_path):
        ctx = PluginContext(
            identity_name="test",
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
        )
        assert ctx.capabilities == {}
