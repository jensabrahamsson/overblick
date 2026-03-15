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
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.event_bus import EventBus
from overblick.core.exceptions import ConfigError
from overblick.core.learning.store import LearningStore
from overblick.core.llm.client import LLMClient
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.orchestrator_bootstrap import OrchestratorBootstrap
from overblick.core.orchestrator_runtime import OrchestratorRuntime
from overblick.core.orchestrator_shutdown import OrchestratorShutdown
from overblick.core.orchestrator_bootstrap_result import OrchestratorBootstrapResult
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.orchestrator_types import OrchestratorState
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_base import (
    AgenticPluginContext,
    CommunicationPluginContext,
    ContentPluginContext,
    DefaultPluginContext,
    MonitoringPluginContext,
    PluginBase,
    PluginContext,
)
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.output_safety import OutputSafety
from overblick.core.security.policy_gate import PolicyGate
from overblick.core.security.preflight import PreflightChecker
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager
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

    # Plugin name → context class mapping (ARCH‑2: narrow per role)
    _PLUGIN_ROLES: ClassVar[dict[str, type[PluginContext]]] = {
        # Agentic plugins
        "github": AgenticPluginContext,
        "dev_agent": AgenticPluginContext,
        "log_agent": AgenticPluginContext,
        # Communication plugins
        "telegram": CommunicationPluginContext,
        "email_agent": CommunicationPluginContext,
        "irc": CommunicationPluginContext,
        # Content plugins
        "moltbook": ContentPluginContext,
        "kontrast": ContentPluginContext,
        "skuggspel": ContentPluginContext,
        "spegel": ContentPluginContext,
        "ai_digest": ContentPluginContext,
        # Monitoring plugins
        "host_health": MonitoringPluginContext,
        "compass": MonitoringPluginContext,
        "stage": MonitoringPluginContext,
        # Default fallback for others
    }

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
        self._state = OrchestratorState.INIT

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

    async def _create_components_via_factory(self) -> dict[str, Any]:
        """Create all components using the factory (if available)."""
        if not self._factory:
            return {}

        return await self._factory.create_all(
            identity_name=self._identity_name,
            base_dir=self._base_dir,
            plugin_names=self._plugin_names,
        )

    def _create_plugin_context(
        self,
        plugin_name: str,
        data_dir: Path,
        log_dir: Path,
        permissions: Any,
        capability_checker: Any,
    ) -> PluginContext:
        """
        Create a role-specific PluginContext for a plugin (ARCH‑2).

        Uses _PLUGIN_ROLES mapping to select appropriate context class.
        Falls back to DefaultPluginContext for unclassified plugins.
        """
        assert self._services.secrets is not None, "Secrets manager must be initialized"
        assert self._services.audit_log is not None, "Audit log must be initialized"

        context_class = self._PLUGIN_ROLES.get(plugin_name, DefaultPluginContext)

        # All context classes have the same constructor signature
        ctx = context_class(
            identity_name=self._identity_name,
            data_dir=data_dir,
            log_dir=log_dir,
            event_bus=self._services.event_bus,
            scheduler=self._services.scheduler,
            audit_log=self._services.audit_log,
            quiet_hours_checker=self._services.quiet_hours,
            llm_pipeline=self._services.llm_pipeline,
            identity=self._services.identity,
            preflight=self._services.preflight,
            output_safety=self._services.output_safety,
            rate_limiter=self._services.rate_limiter,
            permissions=self._services.permissions,
            policy_gate=self._services.policy_gate,
            ipc_client=self._services.ipc_client,
            engagement_db=self._services.engagement_db,
            learning_store=self._services.learning_store,
            get_secret=self._services.secrets.get_secret
            if self._services.secrets
            else lambda x: None,
        )
        return ctx

    def _register_local_plugins(self) -> None:
        """Register local plugins with the registry."""
        try:
            # Try to load local plugins via component factory first
            if self._factory:
                self._factory.register_local_plugins(self._services.registry)
                return

            # Fallback: manual registration
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

        # Load plugins
        await self._load_plugins()

        logger.info("Orchestrator setup completed")

    async def _load_plugins(self) -> None:
        """Load and initialize all requested plugins."""
        if not self._plugin_names:
            logger.warning("No plugins configured for %s", self._identity_name)
            return

        # Resolve requested plugins (identity + constructor)
        plugin_names = self._resolve_requested_plugins()

        # Validate plugin names against registry
        for name in plugin_names:
            if not self._services.registry.is_known(name):
                raise ConfigError(f"Plugin '{name}' is not registered or not allowed")

        # Build contexts and load plugins
        for name in plugin_names:
            try:
                ctx = self._create_plugin_context(
                    plugin_name=name,
                    data_dir=self._paths.data_dir,
                    log_dir=self._paths.log_dir,
                    permissions=self._services.permissions,
                    capability_checker=self._services.capability_checker,
                )

                plugin_cls = self._services.registry.load_plugin(name)
                plugin = plugin_cls(ctx)

                # Run setup
                await plugin.setup()

                # Store plugin
                self._runtime_state.plugins.append(plugin)
                logger.info("Loaded plugin: %s", name)

            except Exception as e:
                logger.error("Failed to load plugin '%s': %s", name, e)
                self._services.audit_log.log(
                    action="plugin_load_failed",
                    details={"plugin": name, "error": str(e)},
                )
                raise

    def _resolve_requested_plugins(self) -> list[str]:
        """Resolve final list of plugins to load (constructor + identity config)."""
        # Start with constructor-provided plugins
        plugins = list(self._plugin_names)

        # Add identity-configured plugins
        if self._services.identity:
            identity_plugins = self._services.identity.raw_config.get("plugins", [])
            plugins.extend([p for p in identity_plugins if p not in plugins])

        return plugins

    async def run(self) -> None:
        """Start the orchestrator's main loop."""
        try:
            await self.setup()
            self._runtime_state.lifecycle_state = OrchestratorState.RUNNING

            self._runtime = OrchestratorRuntime(
                identity_name=self._identity_name,
                services=self._services,
                runtime_state=self._runtime_state,
                on_stop_requested=lambda: self.stop(),
                run_controller=None,  # Will be injected later
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

    def _is_plugin_stopped(self, plugin_name: str) -> bool:
        """Check if a plugin is stopped via control file."""
        if not self._runtime_state.control_file or not self._runtime_state.control_file.exists():
            return False

        # Use cache for performance
        now = time.time()
        if (
            self._runtime_state.control_cache_ts > 0
            and now - self._runtime_state.control_cache_ts < 2.0
        ):
            return self._runtime_state.control_cache.get(plugin_name, "false") == "true"

        try:
            with open(self._runtime_state.control_file, "r") as f:
                data = json.load(f)
                value = data.get(plugin_name, "false")
                self._runtime_state.control_cache[plugin_name] = value
                self._runtime_state.control_cache_ts = now
                return value == "true"
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return False


# Import time here to avoid circular import at module level
import time
