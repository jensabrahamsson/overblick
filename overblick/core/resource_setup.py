from __future__ import annotations

from typing import Any

from overblick.core.learning_store_builder import LearningStoreBuilder
from overblick.core.capability_setup import CapabilitySetup
from overblick.core.ipc_bootstrap import IPCBootstrap
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.identities import Identity


class ResourceSetup:
    def __init__(
        self,
        *,
        services: OrchestratorServices,
        runtime_state: OrchestratorRuntimeState,
        paths: OrchestratorPaths,
        identity_name: str,
    ) -> None:
        self._services = services
        self._runtime_state = runtime_state
        self._paths = paths
        self._identity_name = identity_name

    async def setup(self) -> None:
        """
        Run full resource initialization pipeline:
          - Learning store
          - Capabilities
          - IPC client
        """
        # 1. Learning store
        if self._services.identity:
            builder = LearningStoreBuilder(
                identity=self._services.identity,
                llm_client=self._services.llm_client,
                data_dir=self._paths.data_dir,
            )
            self._services.learning_store = await builder.build()

        # 2. Capabilities
        if self._services.capability_checker and self._services.capability_registry:
            cap_setup = CapabilitySetup(
                registry=self._services.capability_registry,
                services=self._services,
                paths=self._paths,
                identity_name=self._identity_name,
            )
            self._runtime_state.capabilities = await cap_setup.setup()

        # 3. IPC client
        ipc_boot = IPCBootstrap(self._paths.base_dir)
        self._services.ipc_client = ipc_boot.create_client()
