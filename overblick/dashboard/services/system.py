"""
System service — read-only access to global configuration.
"""

import json
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
            logger.error("Failed to load system config: %s", e, exc_info=True)
            return {}

    def get_available_plugins(self) -> list[str]:
        """List available plugins from registry."""
        try:
            from overblick.core.plugin_registry import PluginRegistry
            return PluginRegistry().available_plugins()
        except Exception as e:
            logger.error("Failed to list plugins: %s", e, exc_info=True)
            return []

    def get_capability_bundles(self) -> dict[str, list[str]]:
        """Get capability bundles."""
        try:
            from overblick.capabilities import CAPABILITY_BUNDLES
            return dict(CAPABILITY_BUNDLES)
        except Exception as e:
            logger.error("Failed to load capability bundles: %s", e, exc_info=True)
            return {}

    def get_capability_registry(self) -> list[str]:
        """Get individual capability names."""
        try:
            from overblick.capabilities import CAPABILITY_REGISTRY
            return sorted(CAPABILITY_REGISTRY.keys())
        except Exception as e:
            logger.error("Failed to load capability registry: %s", e, exc_info=True)
            return []

    def get_moltbook_statuses(self) -> list[dict]:
        """Read Moltbook account status files from all identity data dirs."""
        statuses = []
        data_dir = self._base_dir / "data"
        if not data_dir.exists():
            return statuses
        for identity_dir in sorted(data_dir.iterdir()):
            if not identity_dir.is_dir():
                continue
            status_file = identity_dir / "moltbook_status.json"
            if status_file.exists():
                try:
                    status = json.loads(status_file.read_text())
                    status["identity"] = identity_dir.name
                    statuses.append(status)
                except Exception:
                    pass
        return statuses
