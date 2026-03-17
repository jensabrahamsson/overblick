"""
Orchestrator — agent lifecycle manager.

Manages the full lifecycle: INIT -> SETUP -> RUNNING -> STOP.
Wires together identity, plugins, LLM, security, and scheduling.
"""

import asyncio
import importlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from overblick.core.orchestrator_bootstrap import OrchestratorBootstrap
from overblick.core.orchestrator_runtime import OrchestratorRuntime
from overblick.core.orchestrator_shutdown import OrchestratorShutdown
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.orchestrator_types import OrchestratorState
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.plugin_loader import PluginLoader
from overblick.core.plugin_run_controller import PluginRunController
from overblick.identities import Identity
from overblick.supervisor.ipc import IPCClient

if TYPE_CHECKING:
    from overblick.core.component_factory import ComponentFactory

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Top-level agent lifecycle manager.

    Usage:
        orch = Orchestrator(identity_name="anomal")
        await orch.run()  # Blocks until shutdown signal
    """

    def __init__(
        self,
        identity_name: str,
        base_dir: Path | None = None,
        plugins: list[str] | None = None,
        factory: Optional["ComponentFactory"] = None,
    ):
        self._identity_name = identity_name
        self._base_dir = base_dir or Path(__file__).parent.parent.parent
        self._plugin_names = plugins or []

        # Dependency injection for testing
        self._factory = factory

        self._shutdown_event = asyncio.Event()

        # NEW: Own typed containers instead of individual fields
        self._services = OrchestratorServices()
        self._runtime_state = OrchestratorRuntimeState()
        self._paths = OrchestratorPaths.for_identity(self._base_dir, self._identity_name)

        # New modular components
        self._bootstrap: OrchestratorBootstrap | None = None
        self._runtime: OrchestratorRuntime | None = None
        self._shutdown: OrchestratorShutdown | None = None

        # Register local plugins after registry is created
        self._register_local_plugins()

    @property
    def state(self) -> OrchestratorState:
        return self._runtime_state.lifecycle_state

    @property
    def identity(self) -> Identity | None:
        return self._services.identity

    # --- Backward-compatible attribute proxies ---

    @property
    def _identity(self) -> Identity | None:
        return self._services.identity

    @_identity.setter
    def _identity(self, value: Identity | None) -> None:
        self._services.identity = value

    @property
    def _plugins(self) -> list:
        return self._runtime_state.plugins

    @_plugins.setter
    def _plugins(self, value: list) -> None:
        self._runtime_state.plugins = value

    @property
    def _llm_client(self):
        return self._services.llm_client

    @_llm_client.setter
    def _llm_client(self, value) -> None:
        self._services.llm_client = value

    @property
    def _audit_log(self):
        return self._services.audit_log

    @_audit_log.setter
    def _audit_log(self, value) -> None:
        self._services.audit_log = value

    @property
    def _engagement_db_backend(self):
        return self._services.engagement_db_backend

    @_engagement_db_backend.setter
    def _engagement_db_backend(self, value) -> None:
        self._services.engagement_db_backend = value

    @property
    def _registry(self):
        return self._services.registry

    @_registry.setter
    def _registry(self, value) -> None:
        self._services.registry = value

    @property
    def _state(self) -> OrchestratorState:
        return self._runtime_state.lifecycle_state

    @_state.setter
    def _state(self, value: OrchestratorState) -> None:
        self._runtime_state.lifecycle_state = value

    async def _create_components_via_factory(self) -> dict[str, Any]:
        """Create all components using the factory (if available)."""
        if not self._factory:
            return {}

        return await self._factory.create_all(
            identity_name=self._identity_name,
            base_dir=self._base_dir,
            plugin_names=self._plugin_names,
        )

    def _register_local_plugins(self) -> None:
        """Register local plugins with the registry."""
        if self._factory:
            try:
                self._factory.register_local_plugins(self._services.registry)  # type: ignore
            except (AttributeError, Exception) as e:
                logger.debug("Factory local plugin registration skipped: %s", e)
            return

        # Fallback: manual registration when no factory
        try:
            from overblick.plugins._local import register_local_plugins

            register_local_plugins(self._services.registry)
        except ImportError:
            logger.debug("No local plugins found or registered.")
        except Exception as e:
            logger.warning("Failed to register local plugins: %s", e)

    async def setup(self) -> None:
        """Initialize orchestrator and all dependencies."""
        self._runtime_state.lifecycle_state = OrchestratorState.SETUP

        # Bootstrap creates services, runtime_state, paths
        bootstrap = OrchestratorBootstrap(
            identity_name=self._identity_name,
            base_dir=self._base_dir,
            plugin_names=self._plugin_names,
            services=self._services,
            runtime_state=self._runtime_state,
            factory=self._factory,
        )

        result = await bootstrap.setup()
        self._services = result.services
        self._runtime_state = result.runtime_state
        self._paths = result.paths

        self._runtime_state.control_file = self._paths.data_dir / "plugin_control.json"

        # Load plugins via PluginLoader
        loader = PluginLoader(
            identity_name=self._identity_name,
            identity=self._services.identity,
            services=self._services,
            runtime_state=self._runtime_state,
            paths=self._paths,
        )
        # Load plugins via PluginLoader
        if self._services.identity is not None:
            self._runtime_state.plugins = await loader.load_all(self._plugin_names)
        else:
            logger.warning("No identity loaded — skipping plugin loading")

        logger.info("Orchestrator setup completed")

    async def run(self) -> None:
        """Start the orchestrator's main loop."""
        try:
            await self.setup()
            self._runtime_state.lifecycle_state = OrchestratorState.RUNNING

            from overblick.core.plugin_run_controller import PluginRunController

            run_controller = PluginRunController(
                control_file=self._runtime_state.control_file,
            )

            self._runtime = OrchestratorRuntime(
                identity_name=self._identity_name,
                services=self._services,
                runtime_state=self._runtime_state,
                on_stop_requested=lambda: self.stop(),
                run_controller=run_controller,
            )

            await self._runtime.run()
        except asyncio.CancelledError:
            logger.info("Orchestrator run cancelled")
            await self.stop()
        except Exception as e:
            logger.exception("Orchestrator run failed")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Gracefully shut down the orchestrator."""
        if self._runtime_state.lifecycle_state == OrchestratorState.STOPPED:
            return

        self._runtime_state.lifecycle_state = OrchestratorState.STOPPING
        self._runtime_state.shutdown_event.set()

        if self._shutdown is None:
            self._shutdown = OrchestratorShutdown(
                services=self._services,
                runtime_state=self._runtime_state,
            )

        await self._shutdown.shutdown()

        self._runtime_state.lifecycle_state = OrchestratorState.STOPPED
        logger.info("Orchestrator stopped")

    async def _create_llm_client(self) -> Any:
        """Create LLM client — all agents route through the LLM Gateway."""
        from overblick.core.llm.gateway_client import GatewayClient

        assert self._services.identity is not None, "Identity must be loaded before creating LLM client"

        llm_cfg = self._services.identity.llm
        gateway_url = llm_cfg.gateway_url or "http://127.0.0.1:8200"

        with GatewayClient._instantiation_allowed():
            client = GatewayClient(
                base_url=gateway_url,
                model=llm_cfg.model,
                default_priority="low",
                max_tokens=llm_cfg.max_tokens,
                temperature=llm_cfg.temperature,
                top_p=llm_cfg.top_p,
                timeout_seconds=llm_cfg.timeout_seconds,
            )

        if await client.health_check():
            logger.info("Connected to LLM Gateway at %s (model: %s)", gateway_url, llm_cfg.model)
        else:
            logger.warning(
                "LLM Gateway not reachable at %s — agent may have limited functionality",
                gateway_url,
            )

        return client

    def _create_ipc_client(self) -> object | None:
        """Create IPC client searching for supervisor token in standard locations."""
        import os
        import tempfile

        token_name = "overblick-supervisor.token"
        search_dirs: list[Path] = []

        env_dir = os.environ.get("OVERBLICK_IPC_DIR")
        if env_dir:
            search_dirs.append(Path(env_dir))

        search_dirs.append(self._base_dir / "data" / "ipc")
        search_dirs.append(Path(tempfile.gettempdir()) / "overblick")

        socket_dir = None
        token_path = None
        for candidate in search_dirs:
            tp = candidate / token_name
            if tp.exists():
                socket_dir = candidate
                token_path = tp
                logger.debug("Supervisor token found at %s", tp)
                break

        if not token_path:
            logger.debug("No supervisor token found — running in standalone mode")
            return None

        try:
            from overblick.supervisor.ipc import IPCClient, read_ipc_token

            auth_token = read_ipc_token(socket_dir=socket_dir)
            client = IPCClient(
                target="supervisor",
                socket_dir=socket_dir,
                auth_token=auth_token,
            )
            logger.info("IPC client created — supervisor communication enabled")
            return client
        except Exception as e:
            logger.warning("Failed to create IPC client: %s", e)
            return None
