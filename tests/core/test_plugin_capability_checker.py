"""Tests for plugin capability checker."""

import os
import pytest
from unittest.mock import patch
from overblick.core.plugin_capability_checker import PluginCapabilityChecker


def test_check_plugin_no_requirements():
    """Plugin with no required capabilities passes."""
    checker = PluginCapabilityChecker("test_identity", {})
    assert checker.check_plugin("test_plugin", []) is True


def test_check_plugin_all_granted():
    """Plugin with required capabilities all granted passes."""
    config = {
        "plugin_capabilities": {
            "test_plugin": {
                "network_outbound": True,
                "secrets_access": True,
            }
        }
    }
    checker = PluginCapabilityChecker("test_identity", config)
    assert (
        checker.check_plugin("test_plugin", ["network_outbound", "secrets_access"])
        is True
    )


def test_check_plugin_missing_grants_warning():
    """Missing grants trigger warning but plugin loads (non-strict)."""
    config = {"plugin_capabilities": {}}
    checker = PluginCapabilityChecker("test_identity", config)
    with patch(
        "overblick.core.plugin_capability_checker.logger.warning"
    ) as mock_warning:
        result = checker.check_plugin("test_plugin", ["network_outbound"])
        # Should return False because missing grants
        assert result is False
        # Warning logged
        mock_warning.assert_called()


def test_check_plugin_strict_mode_raises():
    """Strict mode raises PermissionError for missing grants."""
    config = {"plugin_capabilities": {}}
    checker = PluginCapabilityChecker("test_identity", config)
    with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
        with pytest.raises(PermissionError) as exc_info:
            checker.check_plugin("test_plugin", ["network_outbound"])
        assert "missing capability grants" in str(exc_info.value)


def test_check_plugin_strict_mode_unknown_capability_raises():
    """Strict mode raises PermissionError for unknown capabilities."""
    config = {"plugin_capabilities": {}}
    checker = PluginCapabilityChecker("test_identity", config)
    with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
        with pytest.raises(PermissionError) as exc_info:
            checker.check_plugin("test_plugin", ["nonexistent_capability"])
        assert "unknown capability" in str(
            exc_info.value
        ).lower() or "missing capability grants" in str(exc_info.value)


def test_check_plugin_strict_mode_granted_passes():
    """Strict mode passes when all grants present."""
    config = {
        "plugin_capabilities": {
            "test_plugin": {
                "network_outbound": True,
            }
        }
    }
    checker = PluginCapabilityChecker("test_identity", config)
    with patch.dict(os.environ, {"OVERBLICK_STRICT_CAPABILITIES": "1"}):
        result = checker.check_plugin("test_plugin", ["network_outbound"])
        assert result is True


def test_check_plugin_unknown_capability_warning():
    """Unknown capability triggers warning."""
    config = {"plugin_capabilities": {}}
    checker = PluginCapabilityChecker("test_identity", config)
    with patch(
        "overblick.core.plugin_capability_checker.logger.warning"
    ) as mock_warning:
        result = checker.check_plugin("test_plugin", ["nonexistent_capability"])
        assert result is False
        mock_warning.assert_called()
