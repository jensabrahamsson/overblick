"""Tests for dashboard system service."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from overblick.dashboard.services.system import SystemService


class TestSystemServiceGetConfig:
    @pytest.fixture
    def svc(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        return SystemService(tmp_path)

    def test_should_return_config_when_file_exists(self, svc, tmp_path):
        config_file = tmp_path / "config" / "overblick.yaml"
        config_file.write_text(yaml.safe_dump({"dashboard": {"port": 9090}}))

        result = svc.get_config()
        assert result["dashboard"]["port"] == 9090

    def test_should_return_empty_dict_when_file_missing(self, tmp_path):
        svc = SystemService(tmp_path)
        result = svc.get_config()
        assert result == {}

    def test_should_return_empty_dict_when_yaml_invalid(self, svc, tmp_path):
        config_file = tmp_path / "config" / "overblick.yaml"
        config_file.write_text(": invalid: yaml: [")

        result = svc.get_config()
        assert result == {}

    def test_should_return_empty_dict_when_yaml_is_empty(self, svc, tmp_path):
        config_file = tmp_path / "config" / "overblick.yaml"
        config_file.write_text("")

        result = svc.get_config()
        assert result == {}


class TestSystemServiceGetAvailablePlugins:
    @pytest.fixture
    def svc(self, tmp_path):
        return SystemService(tmp_path)

    def test_should_return_plugins_list(self, svc):
        mock_registry = MagicMock()
        mock_registry.available_plugins.return_value = ["moltbook", "telegram"]

        with patch("overblick.core.plugin_registry.PluginRegistry", return_value=mock_registry):
            result = svc.get_available_plugins()

        assert result == ["moltbook", "telegram"]

    def test_should_return_empty_on_error(self, svc):
        with patch("overblick.core.plugin_registry.PluginRegistry", side_effect=RuntimeError("boom")):
            result = svc.get_available_plugins()
        assert result == []


class TestSystemServiceGetCapabilityBundles:
    @pytest.fixture
    def svc(self, tmp_path):
        return SystemService(tmp_path)

    def test_should_return_bundles(self, svc):
        mock_bundles = {"knowledge": ["knowledge_loader"], "social": ["openings"]}
        with patch("overblick.capabilities.CAPABILITY_BUNDLES", mock_bundles):
            result = svc.get_capability_bundles()
        assert result == mock_bundles

    def test_should_return_empty_on_error(self, svc):
        with patch("overblick.capabilities.CAPABILITY_BUNDLES", side_effect=AttributeError("missing")):
            # Since CAPABILITY_BUNDLES is accessed as a module attribute, we need
            # to patch the import to raise
            pass

        # Patch the dict() call to cause an exception
        with patch.dict("sys.modules", {"overblick.capabilities": None}):
            result = svc.get_capability_bundles()
        assert result == {}


class TestSystemServiceGetCapabilityRegistry:
    @pytest.fixture
    def svc(self, tmp_path):
        return SystemService(tmp_path)

    def test_should_return_sorted_capability_names(self, svc):
        mock_registry = {"zebra": MagicMock(), "alpha": MagicMock(), "beta": MagicMock()}
        with patch("overblick.capabilities.CAPABILITY_REGISTRY", mock_registry):
            result = svc.get_capability_registry()
        assert result == ["alpha", "beta", "zebra"]

    def test_should_return_empty_on_error(self, svc):
        with patch.dict("sys.modules", {"overblick.capabilities": None}):
            result = svc.get_capability_registry()
        assert result == []


class TestSystemServiceGetMoltbookStatuses:
    @pytest.fixture
    def svc(self, tmp_path):
        return SystemService(tmp_path)

    def test_should_return_statuses_from_data_dirs(self, svc, tmp_path):
        data_dir = tmp_path / "data"
        identity_dir = data_dir / "anomal"
        identity_dir.mkdir(parents=True)
        status = {"account": "anomal_mb", "followers": 42}
        (identity_dir / "moltbook_status.json").write_text(json.dumps(status))

        result = svc.get_moltbook_statuses()
        assert len(result) == 1
        assert result[0]["identity"] == "anomal"
        assert result[0]["followers"] == 42

    def test_should_skip_non_directories(self, svc, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "not_a_dir.txt").write_text("hello")

        result = svc.get_moltbook_statuses()
        assert result == []

    def test_should_skip_dirs_without_status_file(self, svc, tmp_path):
        data_dir = tmp_path / "data"
        (data_dir / "anomal").mkdir(parents=True)

        result = svc.get_moltbook_statuses()
        assert result == []

    def test_should_return_empty_when_no_data_dir(self, tmp_path):
        svc = SystemService(tmp_path)
        result = svc.get_moltbook_statuses()
        assert result == []

    def test_should_handle_invalid_json(self, svc, tmp_path):
        data_dir = tmp_path / "data"
        identity_dir = data_dir / "broken"
        identity_dir.mkdir(parents=True)
        (identity_dir / "moltbook_status.json").write_text("not valid json{{{")

        result = svc.get_moltbook_statuses()
        assert result == []

    def test_should_sort_identity_dirs(self, svc, tmp_path):
        data_dir = tmp_path / "data"
        for name in ["cherry", "anomal", "blixt"]:
            d = data_dir / name
            d.mkdir(parents=True)
            (d / "moltbook_status.json").write_text(json.dumps({"name": name}))

        result = svc.get_moltbook_statuses()
        assert [r["identity"] for r in result] == ["anomal", "blixt", "cherry"]
