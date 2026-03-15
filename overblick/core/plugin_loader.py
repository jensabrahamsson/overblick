from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from overblick.core.capability import CapabilityBase
from overblick.core.local_plugin_config import LocalPluginConfig
from overblick.core.plugin_base import PluginBase
from overblick.core.plugin_context_factory import PluginContextFactory
from overblick.core.plugin_dependency_resolver import PluginDependencyResolver
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.plugin_spec import PluginSpec
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.identities import Identity

logger = logging.getLogger(__name__)


class PluginLoader:
    def __init__(
        self,
        *,
        identity_name: str,
        identity: Identity,
        services: OrchestratorServices,
        runtime_state: OrchestratorRuntimeState,
        paths: OrchestratorPaths,
    ) -> None:
        self._identity_name = identity_name
        self._identity = identity
        self._services = services
        self._runtime_state = runtime_state
        self._paths = paths
        self._registry = services.registry

    async def load_all(
        self,
        requested_plugins: list[str] | None = None,
    ) -> list[PluginBase]:
        """
        Load and initialize all plugins.

        Merges constructor plugins, identity-configured plugins, and local plugins.
        Resolves dependencies and loads in topological order.
        """
        if requested_plugins is None:
            requested_plugins = []

        # 1. Get plugins from identity config
        identity_plugins = []
        if self._identity.raw_config.get("plugins"):
            identity_plugins = list(self._identity.raw_config["plugins"])

        # 2. Get local plugins
        local_config = LocalPluginConfig(self._paths.base_dir, self._identity_name)
        local_plugins = local_config.configured_plugins()

        # 3. Merge with deduplication (preserve order: constructor → identity → local)
        all_plugins = []
        seen = set()
        for name in requested_plugins + identity_plugins + local_plugins:
            if name not in seen:
                all_plugins.append(name)
                seen.add(name)

        if not all_plugins:
            logger.warning("No plugins configured for %s", self._identity_name)
            return []

        # 4. Resolve dependency order
        resolver = PluginDependencyResolver(self._registry)
        try:
            ordered_plugins = resolver.resolve(all_plugins)
        except ValueError as e:
            logger.error("Plugin dependency resolution failed: %s", e)
            raise

        # 5. Create context factory
        factory = PluginContextFactory(
            identity_name=self._identity_name,
            services=self._services,
            paths=self._paths,
        )

        # 6. Load each plugin
        plugins = []
        for name in ordered_plugins:
            try:
                # Create context
                ctx = factory.create(
                    plugin_name=name,
                    permissions=self._services.permissions,
                    capability_checker=self._services.capability_checker,
                )

                # Load plugin class
                plugin_cls = self._registry.load_plugin(name)

                # Instantiate plugin
                plugin = plugin_cls(ctx)

                # Run setup
                await plugin.setup()

                # Store
                plugins.append(plugin)
                logger.info("Loaded plugin: %s", name)

            except Exception as e:
                logger.error("Failed to load plugin '%s': %s", name, e)
                self._services.audit_log.log(
                    action="plugin_load_failed",
                    details={"plugin": name, "error": str(e)},
                )
                raise

        return plugins

    async def load_one(self, plugin_name: str) -> PluginBase:
        """Load a single plugin."""
        plugins = await self.load_all([plugin_name])
        if not plugins:
            raise RuntimeError(f"Plugin '{plugin_name}' failed to load")
        return plugins[0]
