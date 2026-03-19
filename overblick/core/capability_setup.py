from __future__ import annotations

import logging
from typing import Any

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.plugin_base import PluginContext

logger = logging.getLogger(__name__)


class CapabilitySetup:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        services: OrchestratorServices,
        paths: OrchestratorPaths,
        identity_name: str,
    ) -> None:
        if not hasattr(registry, "create"):
            raise TypeError(
                f"CapabilitySetup requires a CapabilityRegistry, got {type(registry).__name__}"
            )
        self._registry = registry
        self._services = services
        self._paths = paths
        self._identity_name = identity_name

    async def setup(self) -> dict[str, CapabilityBase]:
        """
        Initialize and register all required capabilities for this identity.

        Uses the CapabilityRegistry.create(name, ctx, config) API which
        requires a PluginContext for capability initialization.
        """
        if not self._services.identity:
            return {}

        identity = self._services.identity

        # Determine which capabilities to create
        cap_names = list(identity.capability_names) if identity.capability_names else []

        # Fall back to enabled_modules if no explicit capabilities
        if not cap_names and identity.enabled_modules:
            cap_names = list(identity.enabled_modules)

        # Core capabilities injected into ALL agents
        _CORE_CAPS = ["system_clock"]
        for core_cap in _CORE_CAPS:
            if core_cap not in cap_names:
                cap_names.append(core_cap)

        if not cap_names:
            logger.debug("No capabilities configured for %s", self._identity_name)
            return {}

        # Resolve capability names (expanding bundles)
        resolved = self._registry.resolve(cap_names)

        # Build per-capability configs from identity
        from overblick.core.capability import build_capability_configs

        system_prompt = f"You are {identity.display_name}."
        configs = build_capability_configs(identity, system_prompt)

        # Create a temporary PluginContext for capability initialization
        temp_ctx = PluginContext(
            identity_name=self._identity_name,
            data_dir=self._paths.data_dir,
            log_dir=self._paths.log_dir,
            event_bus=self._services.event_bus,
            audit_log=self._services.audit_log,
            quiet_hours_checker=self._services.quiet_hours,
            llm_pipeline=self._services.llm_pipeline,
            identity=self._services.identity,
        )
        # Attach secrets getter (capabilities like 'email' need it)
        if self._services.secrets is not None:
            secrets = self._services.secrets
            temp_ctx._secrets_getter = lambda key, _id=self._identity_name: secrets.get(_id, key)

        capabilities: dict[str, CapabilityBase] = {}
        for name in resolved:
            try:
                cap = self._registry.create(name, temp_ctx, config=configs.get(name, {}))
                if cap:
                    await cap.setup()
                    capabilities[cap.name] = cap
                    logger.info("Capability '%s' created for %s", cap.name, self._identity_name)
            except Exception as e:
                logger.warning(
                    "Capability '%s' setup failed for %s: %s",
                    name,
                    self._identity_name,
                    e,
                )

        return capabilities
