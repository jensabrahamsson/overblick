from __future__ import annotations

from typing import Any

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.plugin_capability_checker import PluginCapabilityChecker


class CapabilitySetup:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        services: OrchestratorServices,
        paths: OrchestratorPaths,
        identity_name: str,
    ) -> None:
        self._registry = registry
        self._services = services
        self._paths = paths
        self._identity_name = identity_name

    async def setup(self) -> dict[str, CapabilityBase]:
        """
        Initialize and register all required capabilities for this identity.

        Reads plugin_capabilities from identity config and instantiates each capability.
        Returns dict mapping capability name → instance.
        """
        # Load capability configuration from identity
        if not self._services.identity:
            return {}

        raw_config = self._services.identity.raw_config
        plugin_caps = raw_config.get("plugin_capabilities", {})

        capabilities: dict[str, CapabilityBase] = {}

        # Iterate over all plugins that have capability grants
        for plugin_name, caps in plugin_caps.items():
            # For each capability granted to this plugin
            for cap_name, enabled in caps.items():
                if not enabled or cap_name in capabilities:
                    continue  # Skip disabled or already registered

                try:
                    # Instantiate capability
                    cap_instance = await self._registry.create_capability(
                        cap_name,
                        identity_name=self._identity_name,
                        config=caps,
                        data_dir=self._paths.data_dir,
                        log_dir=self._paths.log_dir,
                        secrets_manager=self._services.secrets,
                        audit_log=self._services.audit_log,
                    )
                    capabilities[cap_name] = cap_instance
                except Exception as e:
                    # Log but don't fail — capability is optional
                    from logging import getLogger

                    getLogger(__name__).warning(
                        "Failed to instantiate capability '%s' for plugin '%s': %s",
                        cap_name,
                        plugin_name,
                        e,
                    )

        return capabilities
