"""
Plugin discovery and loading.

Finds and instantiates plugins from the overblick.plugins package.
No dynamic code execution — all plugins must be registered explicitly.
"""

import ast
import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Plugin metadata extracted without importing the module."""

    name: str
    module_path: str
    class_name: str
    depends_on: list[str]
    required_capabilities: list[str]
    description: str = ""


def _extract_plugin_metadata(module_path: str, class_name: str) -> PluginMetadata:
    """
    Extract plugin metadata from source file using AST, without importing.

    Args:
        module_path: Importable module path (e.g., "overblick.plugins.ai_digest.plugin")
        class_name: Class name within module

    Returns:
        PluginMetadata with depends_on and required_capabilities.

    Raises:
        FileNotFoundError: If source file cannot be located
        ValueError: If class not found in module
    """
    # Convert module path to file path
    parts = module_path.split(".")
    # Handle overblick.plugins.ai_digest.plugin -> overblick/plugins/ai_digest/plugin.py
    # Determine if we're in a package (directory with __init__.py) or a module
    # For simplicity, assume the last part is the module name and preceding parts are package.
    # Try to locate the file.
    try:
        # First, try to import the module to get its __file__ (safe because we only need path)
        # This executes module code, but we're already importing elsewhere.
        # Instead, we'll compute path relative to sys.path.
        spec = importlib.util.find_spec(module_path)
        if spec is None or spec.origin is None:
            raise FileNotFoundError(f"Cannot locate module {module_path}")
        filepath = Path(spec.origin)
    except Exception as e:
        logger.warning(f"Could not locate module {module_path}: {e}")
        # Fallback: assume it's a .py file in the package tree
        # This is less reliable but works for standard layout.
        base_dir = Path(__file__).parent.parent.parent
        filepath = base_dir / Path(*parts).with_suffix(".py")
        if not filepath.exists():
            raise FileNotFoundError(f"Plugin source not found: {filepath}")

    # Read and parse AST
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find class definition
    class_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_node = node
            break

    if class_node is None:
        raise ValueError(f"Class {class_name} not found in {filepath}")

    # Extract class-level assignments
    depends_on = []
    required_capabilities = []

    for item in class_node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    if target.id == "DEPENDS_ON":
                        # Evaluate the value if it's a constant list
                        if isinstance(item.value, ast.List):
                            for elt in item.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    depends_on.append(elt.value)
                    elif target.id == "REQUIRED_CAPABILITIES":
                        if isinstance(item.value, ast.List):
                            for elt in item.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    required_capabilities.append(elt.value)

    return PluginMetadata(
        name=parts[-2] if len(parts) >= 2 else parts[-1],  # heuristic
        module_path=module_path,
        class_name=class_name,
        depends_on=depends_on,
        required_capabilities=required_capabilities,
        description="",  # Could extract docstring later
    )


# Default registry of known plugins (name -> module path + class name)
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    "ai_digest": ("overblick.plugins.ai_digest.plugin", "AiDigestPlugin"),
    "compass": ("overblick.plugins.compass.plugin", "CompassPlugin"),
    "dev_agent": ("overblick.plugins.dev_agent.plugin", "DevAgentPlugin"),
    "email_agent": ("overblick.plugins.email_agent.plugin", "EmailAgentPlugin"),
    "github": ("overblick.plugins.github.plugin", "GitHubAgentPlugin"),
    "host_health": ("overblick.plugins.host_health.plugin", "HostHealthPlugin"),
    "irc": ("overblick.plugins.irc.plugin", "IRCPlugin"),
    "kontrast": ("overblick.plugins.kontrast.plugin", "KontrastPlugin"),
    "log_agent": ("overblick.plugins.log_agent.plugin", "LogAgentPlugin"),
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "polymarket_monitor": (
        "overblick.plugins.polymarket_monitor.plugin",
        "PolymarketMonitorPlugin",
    ),
    "skuggspel": ("overblick.plugins.skuggspel.plugin", "SkuggspelPlugin"),
    "spegel": ("overblick.plugins.spegel.plugin", "SpegelPlugin"),
    "stage": ("overblick.plugins.stage.plugin", "StagePlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "whallet_trader": ("overblick.plugins.whallet_trader.plugin", "WhalletTraderPlugin"),
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

    def get_plugin_class(self, name: str) -> type[PluginBase]:
        """
        Get plugin class without instantiating it.

        Useful for reading metadata like DEPENDS_ON before instantiation.

        Args:
            name: Plugin name

        Returns:
            Plugin class

        Raises:
            ValueError: If plugin name is unknown
            ImportError: If module cannot be imported
        """
        if name not in self._plugins:
            raise ValueError(
                f"Unknown plugin: '{name}'. Available: {', '.join(self._plugins.keys())}"
            )

        module_path, class_name = self._plugins[name]

        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Failed to load plugin class '{name}': {e}") from e

        if not issubclass(cls, PluginBase):
            raise TypeError(f"Plugin '{name}' class {cls} is not a subclass of PluginBase")

        return cls

    def get_plugin_metadata(self, name: str) -> PluginMetadata:
        """
        Extract plugin metadata without importing the plugin module.

        Uses AST parsing to read DEPENDS_ON and REQUIRED_CAPABILITIES.

        Args:
            name: Plugin name

        Returns:
            PluginMetadata

        Raises:
            ValueError: If plugin name is unknown
            FileNotFoundError: If source file cannot be located
        """
        if name not in self._plugins:
            raise ValueError(
                f"Unknown plugin: '{name}'. Available: {', '.join(self._plugins.keys())}"
            )

        module_path, class_name = self._plugins[name]
        return _extract_plugin_metadata(module_path, class_name)

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
        cls = self.get_plugin_class(name)

        plugin = cls(ctx)
        self._loaded[name] = plugin
        logger.info(f"PluginRegistry: loaded '{name}' ({cls.__name__})")
        return plugin

    def get(self, name: str) -> PluginBase | None:
        """Get a loaded plugin by name."""
        return self._loaded.get(name)

    def all_loaded(self) -> dict[str, PluginBase]:
        """Get all loaded plugins."""
        return dict(self._loaded)

    def available_plugins(self) -> list[str]:
        """List all known plugin names."""
        return sorted(self._plugins.keys())
