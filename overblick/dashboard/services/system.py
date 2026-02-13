"""
System service — read-only access to global configuration.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SystemService:
    """Read-only access to system-level configuration."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._config_path = base_dir / "config" / "overblick.yaml"

    def get_config(self) -> dict[str, Any]:
        """Load system config from config/overblick.yaml."""
        if not self._config_path.exists():
            return {}
        try:
            with open(self._config_path) as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Failed to load system config: %s", e)
            return {}

    def get_available_plugins(self) -> list[str]:
        """List available plugins from registry."""
        try:
            from overblick.core.plugin_registry import PluginRegistry
            return PluginRegistry.available_plugins()
        except Exception as e:
            logger.error("Failed to list plugins: %s", e)
            return []

    def get_capability_bundles(self) -> dict[str, list[str]]:
        """Get capability bundles."""
        try:
            from overblick.capabilities import CAPABILITY_BUNDLES
            return dict(CAPABILITY_BUNDLES)
        except Exception as e:
            logger.error("Failed to load capability bundles: %s", e)
            return {}

    def get_capability_registry(self) -> list[str]:
        """Get individual capability names."""
        try:
            from overblick.capabilities import CAPABILITY_REGISTRY
            return sorted(CAPABILITY_REGISTRY.keys())
        except Exception as e:
            logger.error("Failed to load capability registry: %s", e)
            return []
