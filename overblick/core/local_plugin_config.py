from __future__ import annotations

from pathlib import Path
from typing import Any

from overblick.core.plugin_registry import PluginRegistry


class LocalPluginConfig:
    def __init__(self, base_dir: Path, identity_name: str) -> None:
        self._base_dir = base_dir
        self._identity_name = identity_name
        self._config_path = base_dir / "config" / "local_plugins.yaml"

    def configured_plugins(self) -> list[str]:
        """Return list of plugins configured for this identity in local_plugins.yaml."""
        try:
            import yaml

            if not self._config_path.exists():
                return []

            with open(self._config_path) as f:
                config = yaml.safe_load(f)

            if not isinstance(config, dict):
                return []

            identity_config = config.get(self._identity_name, {})
            return identity_config.get("plugins", [])
        except Exception as e:
            # Log but don't crash — local plugins are optional
            from logging import getLogger

            getLogger(__name__).debug(
                "Failed to load local plugin config for %s: %s", self._identity_name, e
            )
            return []

    def register_local_plugins(self, registry: PluginRegistry) -> None:
        """Register all local plugins found in the local_plugins.yaml file."""
        try:
            import yaml

            if not self._config_path.exists():
                return

            with open(self._config_path) as f:
                config = yaml.safe_load(f)

            if not isinstance(config, dict):
                return

            # Register each plugin entry under 'plugins'
            plugins_section = config.get("plugins", {})
            for plugin_name, plugin_def in plugins_section.items():
                if (
                    isinstance(plugin_def, dict)
                    and "module" in plugin_def
                    and "class" in plugin_def
                ):
                    module_path = plugin_def["module"]
                    class_name = plugin_def["class"]
                    registry.register_local_plugin(plugin_name, module_path, class_name)
        except Exception as e:
            from logging import getLogger

            getLogger(__name__).warning("Failed to register local plugins: %s", e)
