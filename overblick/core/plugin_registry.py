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

# Default registry of known plugins (name -> module path + class name)
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    "ai_digest": ("overblick.plugins.ai_digest.plugin", "AiDigestPlugin"),
    "discord": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "rss": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "webhook": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
    "host_health": ("overblick.plugins.host_health.plugin", "HostHealthPlugin"),
    "email_agent": ("overblick.plugins.email_agent.plugin", "EmailAgentPlugin"),
    "irc": ("overblick.plugins.irc.plugin", "IRCPlugin"),
}

# Module-level alias for backward compatibility (tests import this)
_KNOWN_PLUGINS = _DEFAULT_PLUGINS


class PluginRegistry:
    """
    Plugin registry — discovers and instantiates plugins.

    Security: Only loads from the known plugins whitelist.
    No dynamic imports from user input or network.

    Each instance gets its own copy of the default plugins dict
    to prevent cross-instance pollution during testing.
    """

    def __init__(self):
        self._loaded: dict[str, PluginBase] = {}
        self._plugins: dict[str, tuple[str, str]] = dict(_DEFAULT_PLUGINS)

    def register(self, name: str, module_path: str, class_name: str) -> None:
        """
        Register a plugin class (for testing or extensions).

        Args:
            name: Plugin name
            module_path: Importable module path
            class_name: Class name within module
        """
        self._plugins[name] = (module_path, class_name)
        # Also update module-level dict for backward compatibility
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
        if name not in self._plugins:
            raise ValueError(
                f"Unknown plugin: '{name}'. "
                f"Available: {', '.join(self._plugins.keys())}"
            )

        module_path, class_name = self._plugins[name]

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

    def available_plugins(self) -> list[str]:
        """List all known plugin names."""
        return sorted(self._plugins.keys())
