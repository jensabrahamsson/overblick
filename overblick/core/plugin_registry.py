"""
Plugin discovery and loading.

Finds and instantiates plugins from the overblick.plugins package.
No dynamic code execution — all plugins must be registered explicitly.
"""

import importlib
import logging
from typing import Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)

# Registry of known plugins/connectors (name -> module path + class name)
_KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "gmail": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "discord": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "rss": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "webhook": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
    # Connector aliases (same classes, new names)
    "moltbook_connector": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "telegram_connector": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "gmail_connector": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "discord_connector": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix_connector": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "rss_connector": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "webhook_connector": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
}


class PluginRegistry:
    """
    Plugin registry — discovers and instantiates plugins.

    Security: Only loads from the _KNOWN_PLUGINS whitelist.
    No dynamic imports from user input or network.
    """

    def __init__(self):
        self._loaded: dict[str, PluginBase] = {}

    def register(self, name: str, module_path: str, class_name: str) -> None:
        """
        Register a plugin class (for testing or extensions).

        Args:
            name: Plugin name
            module_path: Importable module path
            class_name: Class name within module
        """
        _KNOWN_PLUGINS[name] = (module_path, class_name)
        logger.info(f"PluginRegistry: registered '{name}' -> {module_path}.{class_name}")

    def load(self, name: str, ctx: PluginContext) -> PluginBase:
        """
        Load and instantiate a plugin by name.

        Args:
            name: Plugin name (must be in known plugins)
            ctx: Plugin context

        Returns:
            Instantiated plugin

        Raises:
            ValueError: If plugin name is unknown
            ImportError: If module cannot be imported
        """
        if name not in _KNOWN_PLUGINS:
            raise ValueError(
                f"Unknown plugin: '{name}'. "
                f"Available: {', '.join(_KNOWN_PLUGINS.keys())}"
            )

        module_path, class_name = _KNOWN_PLUGINS[name]

        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Failed to load plugin '{name}': {e}") from e

        if not issubclass(cls, PluginBase):
            raise TypeError(f"Plugin class {class_name} must inherit from PluginBase")

        plugin = cls(ctx)
        self._loaded[name] = plugin
        logger.info(f"PluginRegistry: loaded '{name}' ({class_name})")
        return plugin

    def get(self, name: str) -> Optional[PluginBase]:
        """Get a loaded plugin by name."""
        return self._loaded.get(name)

    def all_loaded(self) -> dict[str, PluginBase]:
        """Get all loaded plugins."""
        return dict(self._loaded)

    @staticmethod
    def available_plugins() -> list[str]:
        """List all known plugin names."""
        return sorted(_KNOWN_PLUGINS.keys())


# Connector alias — new naming convention (backward-compatible)
ConnectorRegistry = PluginRegistry
