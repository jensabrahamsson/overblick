"""
Orchestrator — agent lifecycle manager.

Manages the full lifecycle: INIT -> SETUP -> RUNNING -> STOP.
Wires together identity, plugins, LLM, security, and scheduling.
"""

import asyncio
import importlib
import json
import logging
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.event_bus import EventBus
from overblick.core.exceptions import ConfigError
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.llm.client import LLMClient
from overblick.supervisor.ipc import IPCClient
from overblick.core.learning.store import LearningStore
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_base import (
    PluginBase,
    PluginContext,
    AgenticPluginContext,
    CommunicationPluginContext,
    ContentPluginContext,
    MonitoringPluginContext,
    DefaultPluginContext,
)
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler, TaskPriority
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.output_safety import OutputSafety
from overblick.core.security.preflight import PreflightChecker
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager
from overblick.identities import Identity, load_identity

from typing import TYPE_CHECKING, Any, Optional, Dict, List, Set

if TYPE_CHECKING:
    from overblick.core.component_factory import ComponentFactory

logger = logging.getLogger(__name__)


class OrchestratorState(Enum):
    """Orchestrator lifecycle states."""

    INIT = "init"
    SETUP = "setup"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class Orchestrator:
    """
    Top-level agent lifecycle manager.

    Usage:
        orch = Orchestrator(identity_name="anomal")
        await orch.run()  # Blocks until shutdown signal
    """

    # Plugin name → context class mapping (ARCH‑2: narrow per role)
    _PLUGIN_ROLES: dict[str, type[PluginContext]] = {
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

        # Framework components (initialized in setup)
        self._identity: Identity | None = None
        self._event_bus = EventBus()
        self._scheduler = Scheduler()
        self._registry = PluginRegistry()
        self._register_local_plugins()
        self._audit_log: AuditLog | None = None
        self._secrets: SecretsManager | None = None
        self._quiet_hours: QuietHoursChecker | None = None
        self._llm_client: LLMClient | None = None
        self._llm_pipeline: SafeLLMPipeline | None = None
        self._preflight: PreflightChecker | None = None
        self._output_safety: OutputSafety | None = None
        self._rate_limiter: RateLimiter | None = None
        self._plugins: list[PluginBase] = []
        self._capabilities: dict[str, CapabilityBase] = {}
        self._ipc_client: IPCClient | None = None
        self._engagement_db_backend: SQLiteBackend | None = None
        self._engagement_db: EngagementDB | None = None
        self._learning_store: LearningStore | None = None
        self._control_file: Path | None = None
        self._control_cache: dict[str, str] = {}
        self._control_cache_ts: float = 0.0

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def identity(self) -> Identity | None:
        return self._identity

    async def _create_components_via_factory(self) -> dict[str, Any]:
        """Create all components using the factory (if available)."""
        if not self._factory:
            # Fall back to manual creation
            return {}

        # Create components using factory
        identity = await self._factory.load_identity()
        paths = self._factory.get_paths()

        secrets = self._factory.create_secrets_manager()
        audit_log = self._factory.create_audit_log()
        engagement_db = await self._factory.create_engagement_db(identity)
        quiet_hours = self._factory.create_quiet_hours_checker(identity)
        llm_client = await self._factory.create_llm_client(identity)
        preflight = self._factory.create_preflight_checker(identity, llm_client)
        output_safety = self._factory.create_output_safety(identity)
        rate_limiter = self._factory.create_rate_limiter(identity)

        # Create pipeline with dependencies
        llm_pipeline = self._factory.create_safe_llm_pipeline(
            llm_client=llm_client,
            audit_log=audit_log,
            preflight_checker=preflight,
            output_safety=output_safety,
            rate_limiter=rate_limiter,
            identity=identity,
        )

        # Create other components
        permissions = self._factory.create_permission_checker(identity)
        capability_checker = self._factory.create_plugin_capability_checker(identity)
        ipc_client = self._factory.create_ipc_client()

        # Framework core components
        event_bus = self._factory.create_event_bus()
        scheduler = self._factory.create_scheduler()
        registry = self._factory.create_plugin_registry()

        return {
            "identity": identity,
            "paths": paths,
            "secrets": secrets,
            "audit_log": audit_log,
            "engagement_db": engagement_db,
            "quiet_hours": quiet_hours,
            "llm_client": llm_client,
            "preflight": preflight,
            "output_safety": output_safety,
            "rate_limiter": rate_limiter,
            "llm_pipeline": llm_pipeline,
            "permissions": permissions,
            "capability_checker": capability_checker,
            "ipc_client": ipc_client,
            "event_bus": event_bus,
            "scheduler": scheduler,
            "registry": registry,
        }

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
        assert self._secrets is not None, "Secrets manager must be initialized"
        assert self._audit_log is not None, "Audit log must be initialized"

        context_class = self._PLUGIN_ROLES.get(plugin_name, DefaultPluginContext)

        # All context classes have the same constructor signature
        ctx = context_class(
            identity_name=self._identity_name,
            data_dir=data_dir,
            log_dir=log_dir,
            event_bus=self._event_bus,
            scheduler=self._scheduler,
            audit_log=self._audit_log,
            quiet_hours_checker=self._quiet_hours,
            llm_pipeline=self._llm_pipeline,
            identity=self._identity,
            preflight_checker=self._preflight,
            output_safety=self._output_safety,
            permissions=permissions,
            capabilities=self._capabilities,
            ipc_client=self._ipc_client,
            engagement_db=self._engagement_db,
            learning_store=self._learning_store,
        )
        # Set raw LLM client via property (security‑checked)
        ctx.llm_client = self._llm_client
        secrets = self._secrets
        assert secrets is not None
        ctx._secrets_getter = lambda key, _id=self._identity_name: secrets.get(_id, key)
        return ctx

    async def setup(self) -> None:
        """Initialize all framework components and plugins."""
        self._state = OrchestratorState.SETUP
        logger.info(f"Setting up Överblick orchestrator for identity: {self._identity_name}")

        # Try to use factory first
        factory_components = await self._create_components_via_factory()
        use_factory = bool(factory_components)

        # Variables that need to be defined in both branches
        data_dir: Path | None = None
        log_dir: Path | None = None
        secrets_dir: Path | None = None
        permissions: Any | None = None
        capability_checker: Any | None = None

        if use_factory:
            # Extract components from factory
            self._identity = factory_components["identity"]
            paths = factory_components["paths"]
            self._secrets = factory_components["secrets"]
            self._audit_log = factory_components["audit_log"]
            self._engagement_db = factory_components["engagement_db"]
            self._quiet_hours = factory_components["quiet_hours"]
            self._llm_client = factory_components["llm_client"]  # type: ignore
            self._preflight = factory_components["preflight"]
            self._output_safety = factory_components["output_safety"]
            self._rate_limiter = factory_components["rate_limiter"]
            self._llm_pipeline = factory_components["llm_pipeline"]
            permissions = factory_components["permissions"]
            capability_checker = factory_components["capability_checker"]
            self._ipc_client = factory_components["ipc_client"]  # type: ignore
            self._event_bus = factory_components["event_bus"]
            self._scheduler = factory_components["scheduler"]
            self._registry = factory_components["registry"]

            data_dir = paths["data_dir"]
            log_dir = paths["log_dir"]
            secrets_dir = paths["secrets_dir"]
        else:
            # 1. Load identity
            self._identity = load_identity(self._identity_name)
            logger.info(f"Identity loaded: {self._identity.display_name} v{self._identity.version}")

            # 2. Setup paths
            data_dir = self._base_dir / "data" / self._identity_name
            log_dir = self._base_dir / "logs" / self._identity_name
            secrets_dir = self._base_dir / "config" / "secrets"

            data_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)

        assert data_dir is not None and log_dir is not None and secrets_dir is not None, (
            "Paths must be defined"
        )
        assert self._identity is not None, "Identity must be loaded by this point"
        # 2b. Plugin control file (per-agent stop/start from dashboard)"
        self._control_file = data_dir / "plugin_control.json"

        if not use_factory:
            # 3. Initialize security
            assert data_dir is not None
            assert secrets_dir is not None
            self._secrets = SecretsManager(secrets_dir)
            self._audit_log = AuditLog(data_dir / "audit.db", self._identity_name)
            self._audit_log.log("orchestrator_setup", category="lifecycle")

            # 3b. Initialize engagement database (lazy — only if moltbook is active)
            plugin_names = list(self._identity.plugins) if self._identity.plugins else []
            if "moltbook" in plugin_names:
                eng_db_config = DatabaseConfig(
                    sqlite_path=str(data_dir / "engagement.db"),
                )
                self._engagement_db_backend = SQLiteBackend(
                    eng_db_config, identity=self._identity_name
                )
                await self._engagement_db_backend.connect()
                self._engagement_db = EngagementDB(
                    self._engagement_db_backend, identity=self._identity_name
                )
                await self._engagement_db.setup()
                logger.info("EngagementDB initialized for %s", self._identity_name)
            else:
                logger.debug(
                    "EngagementDB skipped — no moltbook plugin for %s", self._identity_name
                )

            # 4. Initialize quiet hours
            self._quiet_hours = QuietHoursChecker(self._identity.quiet_hours)

            # 5. Initialize LLM client
            self._llm_client = await self._create_llm_client()  # type: ignore

            # 6. Initialize security subsystems
            self._preflight = self._create_preflight()
            self._output_safety = self._create_output_safety()
            self._rate_limiter = RateLimiter(
                max_tokens=self._identity.security.rate_limiter_max_tokens,
                refill_rate=self._identity.security.rate_limiter_refill_rate,
            )

            # 7. Create safe LLM pipeline
            self._llm_pipeline = SafeLLMPipeline(
                llm_client=self._llm_client,
                audit_log=self._audit_log,
                preflight_checker=self._preflight,
                output_safety=self._output_safety,
                rate_limiter=self._rate_limiter,
                identity_name=self._identity_name,
                strict=True,  # Main agent pipeline uses full security
            )
            logger.info("SafeLLMPipeline initialized with full security chain")

            # 8. Create permissions and capability checker for plugin loading
            permissions = PermissionChecker.from_identity(self._identity)
            capability_checker = PluginCapabilityChecker(
                identity_name=self._identity_name,
                raw_config=self._identity.raw_config,
            )

        # Ensure required variables are set
        assert data_dir is not None and log_dir is not None, "Paths must be defined"
        assert permissions is not None, "Permissions must be defined"
        assert capability_checker is not None, "Capability checker must be defined"

        # 9. Create shared capabilities (orchestrator-level)
        await self._setup_capabilities()

        # 10. Initialize per-identity learning store
        await self._setup_learning_store(data_dir)

        # 11. Create IPC client (if running under supervisor)
        if self._ipc_client is None:
            self._ipc_client = self._create_ipc_client()  # type: ignore[assignment]

        # Use plugins from identity if specified, otherwise fall back to constructor arg
        plugin_names = (
            list(self._identity.plugins) if self._identity.plugins else self._plugin_names
        )

        # Append any local plugins configured for this identity
        local_plugins = self._load_local_plugin_config()
        for lp in local_plugins:
            if lp not in plugin_names:
                plugin_names.append(lp)

        # Resolve plugin dependencies (ARCH-4)
        try:
            plugin_names = self._resolve_plugin_dependencies(plugin_names)
            logger.info(f"Plugins sorted by dependencies: {plugin_names}")
        except Exception as e:
            logger.warning(f"Failed to resolve plugin dependencies: {e}")
            # Continue with original order (best effort)

        for plugin_name in plugin_names:
            ctx = self._create_plugin_context(
                plugin_name=plugin_name,
                data_dir=data_dir / plugin_name,
                log_dir=log_dir,
                permissions=permissions,
                capability_checker=capability_checker,
            )

            try:
                plugin = self._registry.load(plugin_name, ctx)

                # Check plugin capabilities (warnings only for beta)
                if hasattr(plugin, "REQUIRED_CAPABILITIES"):
                    capability_checker.check_plugin(plugin_name, plugin.REQUIRED_CAPABILITIES)

                await plugin.setup()
                self._plugins.append(plugin)
                self._audit_log.log(
                    "plugin_loaded",
                    category="lifecycle",
                    plugin=plugin_name,
                )
                logger.info(f"Plugin '{plugin_name}' loaded and ready")
            except Exception as e:
                logger.error(f"Failed to load plugin '{plugin_name}': {e}", exc_info=True)
                self._audit_log.log(
                    "plugin_load_failed",
                    category="lifecycle",
                    plugin=plugin_name,
                    success=False,
                    error=str(e),
                )

        if not self._plugins:
            raise ConfigError("No plugins loaded — cannot start")

        logger.info(f"Setup complete: {len(self._plugins)} plugin(s) active")

    async def run(self) -> None:
        """
        Main run loop. Blocks until shutdown signal.
        """
        try:
            await self.setup()
        except Exception:
            # Clean up any partially initialized resources
            await self.stop()
            raise
        self._state = OrchestratorState.RUNNING

        # Register signal handlers — cross-platform (Unix signals / Windows signal.signal)
        from overblick.shared.platform import register_shutdown_signals

        register_shutdown_signals(self._shutdown_event)

        self._audit_log.log("orchestrator_started", category="lifecycle")
        self._audit_log.start_background_cleanup()

        if self._engagement_db:
            self._engagement_db.start_background_cleanup()

        logger.info(f"Överblick orchestrator running as '{self._identity.display_name}'")
        print(f"\n  [ Överblick ] {self._identity.display_name} is awake.\n")

        try:
            # Register plugin ticks in scheduler (guarded by control file)
            for plugin in self._plugins:
                interval = self._identity.schedule.feed_poll_minutes * 60

                async def _guarded_tick(p=plugin):
                    import time as _time

                    logger.debug("Guarded tick starting for '%s'", p.name)
                    if await self._is_plugin_stopped(p.name):
                        logger.debug("Agent '%s' stopped via control file, skipping tick", p.name)
                        return
                    tick_start = _time.monotonic()
                    await p.tick()
                    tick_ms = (_time.monotonic() - tick_start) * 1000
                    logger.debug("Guarded tick completed for '%s' (%.1fms)", p.name, tick_ms)
                    await self._event_bus.emit(
                        "plugin_tick",
                        plugin=p.name,
                        identity=self._identity_name,
                        duration_ms=tick_ms,
                    )

                await self._scheduler.add(
                    f"tick_{plugin.name}",
                    _guarded_tick,
                    interval_seconds=interval,
                    run_immediately=True,
                    priority=TaskPriority.LOW,
                )

                # Schedule heartbeat if plugin supports it (e.g. MoltbookPlugin)
                if callable(getattr(plugin, "post_heartbeat", None)):
                    heartbeat_interval = self._identity.schedule.heartbeat_hours * 3600

                    async def _guarded_heartbeat(p=plugin):
                        if await self._is_plugin_stopped(p.name):
                            return
                        await p.post_heartbeat()

                    await self._scheduler.add(
                        f"heartbeat_{plugin.name}",
                        _guarded_heartbeat,
                        interval_seconds=heartbeat_interval,
                        run_immediately=False,
                        priority=TaskPriority.HIGH,
                    )
                    logger.info(
                        "Heartbeat scheduled for '%s' every %dh",
                        plugin.name,
                        self._identity.schedule.heartbeat_hours,
                    )

            # Run scheduler and shutdown event concurrently — first to complete wins
            scheduler_task = asyncio.create_task(self._scheduler.start())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())

            _done, pending = await asyncio.wait(
                {scheduler_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            logger.info("Orchestrator cancelled")
        except Exception as e:
            logger.error(f"Orchestrator error: {e}", exc_info=True)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stop everything."""
        if self._state == OrchestratorState.STOPPING:
            return  # Prevent double-stop

        self._state = OrchestratorState.STOPPING
        logger.info("Orchestrator stopping...")

        # Stop scheduler
        await self._scheduler.stop()

        # Stop background cleanups
        if self._audit_log:
            self._audit_log.stop_background_cleanup()
        if self._engagement_db:
            self._engagement_db.stop_background_cleanup()

        # Teardown plugins (reverse order)
        for plugin in reversed(self._plugins):
            try:
                await plugin.teardown()
                logger.info(f"Plugin '{plugin.name}' torn down")
            except Exception as e:
                logger.error(f"Error tearing down '{plugin.name}': {e}", exc_info=True)

        # Close LLM client
        if self._llm_client and hasattr(self._llm_client, "close"):
            try:
                await self._llm_client.close()
            except Exception as e:
                logger.error(f"Error closing LLM client: {e}", exc_info=True)

        # Close engagement DB backend
        if self._engagement_db_backend:
            try:
                await self._engagement_db_backend.close()
            except Exception as e:
                logger.error("Error closing engagement DB backend: %s", e, exc_info=True)

        # Final audit log
        if self._audit_log:
            self._audit_log.log("orchestrator_stopped", category="lifecycle")
            self._audit_log.close()

        # Cleanup event bus
        self._event_bus.clear()

        self._state = OrchestratorState.STOPPED
        logger.info("Orchestrator stopped cleanly")

    async def _is_plugin_stopped(self, plugin_name: str) -> bool:
        """Check if a plugin is stopped via the dashboard control file (cached for 10s)."""
        if not self._control_file:
            return False

        import time as _time

        now = _time.monotonic()
        # Use cache if valid (10s TTL)
        if now - self._control_cache_ts < 10.0:
            return self._control_cache.get(plugin_name) == "stopped"

        # Refresh cache
        try:
            if await asyncio.to_thread(self._control_file.exists):
                text = await asyncio.to_thread(self._control_file.read_text)
                self._control_cache = json.loads(text)
            else:
                self._control_cache = {}
            self._control_cache_ts = now
            return self._control_cache.get(plugin_name) == "stopped"
        except Exception as e:
            logger.debug("Could not read plugin control file: %s", e)
        return False

    async def _setup_learning_store(self, data_dir: Path) -> None:
        """Initialize the per-identity LearningStore with ethos gating."""
        from overblick.core.learning import LearningStore

        # Extract ethos text from identity
        ethos = self._identity.raw_config.get("ethos", [])
        if isinstance(ethos, list):
            ethos_text = "\n".join(str(e) for e in ethos)
        else:
            ethos_text = str(ethos) if ethos else ""

        # Fall back to ethos_text field if ethos list is empty
        if not ethos_text:
            ethos_text = self._identity.raw_config.get("ethos_text", "")

        # Build embed_fn from gateway client if available
        embed_fn = self._get_embed_fn()

        db_path = data_dir / "learnings.db"
        self._learning_store = LearningStore(
            db_path=db_path,
            ethos_text=ethos_text,
            llm_pipeline=self._llm_pipeline,
            embed_fn=embed_fn,
        )
        await self._learning_store.setup()
        logger.info(
            "LearningStore initialized for %s (embeddings=%s)",
            self._identity_name,
            embed_fn is not None,
        )

    def _get_embed_fn(self):
        """Create an embedding function from the LLM client if it supports embeddings."""
        if self._llm_client and hasattr(self._llm_client, "embed"):

            async def _embed(text: str) -> list[float]:
                return await self._llm_client.embed(text)

            return _embed
        return None

    async def _setup_capabilities(self) -> None:
        """Create shared capabilities at the orchestrator level."""
        # Determine which capabilities to create
        cap_names = list(self._identity.capability_names) if self._identity.capability_names else []

        # Fall back to enabled_modules if no explicit capabilities
        if not cap_names and self._identity.enabled_modules:
            cap_names = list(self._identity.enabled_modules)

        # Core capabilities injected into ALL agents (always available)
        _CORE_CAPS = ["system_clock"]
        for core_cap in _CORE_CAPS:
            if core_cap not in cap_names:
                cap_names.append(core_cap)

        if not cap_names:
            logger.debug("No capabilities configured for %s", self._identity_name)
            return

        try:
            registry = CapabilityRegistry.default()
        except Exception as e:
            logger.warning("Could not load capability registry: %s", e)
            return

        # Build per-capability configs from identity (centralized)
        from overblick.core.capability import build_capability_configs

        system_prompt = f"You are {self._identity.display_name}."
        configs = build_capability_configs(self._identity, system_prompt)

        # Create a temporary PluginContext for capability creation
        # (capabilities need a context but aren't plugin-specific)
        data_dir = self._base_dir / "data" / self._identity_name
        temp_ctx = PluginContext(
            identity_name=self._identity_name,
            data_dir=data_dir,
            log_dir=self._base_dir / "logs" / self._identity_name,
            llm_client=self._llm_client,
            event_bus=self._event_bus,
            audit_log=self._audit_log,
            quiet_hours_checker=self._quiet_hours,
            llm_pipeline=self._llm_pipeline,
            identity=self._identity,
        )
        # Attach secrets getter (capabilities like 'email' need it)
        temp_ctx._secrets_getter = lambda key, _id=self._identity_name: self._secrets.get(_id, key)

        resolved = registry.resolve(cap_names)
        for name in resolved:
            cap = registry.create(name, temp_ctx, config=configs.get(name, {}))
            if cap:
                try:
                    await cap.setup()
                    self._capabilities[cap.name] = cap
                    logger.info("Orchestrator capability '%s' created", cap.name)
                except Exception as e:
                    logger.warning("Capability '%s' setup failed: %s", name, e)

        logger.info("Orchestrator created %d shared capabilities", len(self._capabilities))

    async def _create_llm_client(self) -> LLMClient:
        """Create LLM client — all agents route through the LLM Gateway.

        The gateway handles backend routing (local Ollama, cloud Ollama, OpenAI)
        based on its own configuration. Agents only need to know the gateway URL.
        """
        from overblick.core.llm.gateway_client import GatewayClient

        llm_cfg = self._identity.llm
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
            logger.info(
                "Connected to LLM Gateway at %s (model: %s)",
                gateway_url,
                llm_cfg.model,
            )
        else:
            logger.warning(
                "LLM Gateway not reachable at %s — agent may have limited functionality",
                gateway_url,
            )

        return client

    def _create_preflight(self) -> PreflightChecker | None:
        """Create preflight checker from identity security config."""
        if not self._identity.security.enable_preflight:
            logger.info("Preflight checker disabled by identity config")
            return None

        admin_ids = set(self._identity.security.admin_user_ids)
        deflections = (
            self._identity.deflections if isinstance(self._identity.deflections, dict) else {}
        )

        return PreflightChecker(
            llm_client=self._llm_client,
            admin_user_ids=admin_ids,
            deflections=deflections,
        )

    def _create_ipc_client(self) -> object | None:
        """
        Create an IPC client if a supervisor token file exists.

        Searches for the supervisor token in priority order:
        1. OVERBLICK_IPC_DIR env var (set by supervisor for child processes)
        2. Project-based path: <base_dir>/data/ipc/
        3. System temp: /tmp/overblick/ (legacy default)

        Returns:
            IPCClient if supervisor token exists, None otherwise.
        """
        import os
        import tempfile

        token_name = "overblick-supervisor.token"

        # Build search paths in priority order
        search_dirs: list[Path] = []

        env_dir = os.environ.get("OVERBLICK_IPC_DIR")
        if env_dir:
            search_dirs.append(Path(env_dir))

        search_dirs.append(self._base_dir / "data" / "ipc")
        search_dirs.append(Path(tempfile.gettempdir()) / "overblick")

        # Find first directory containing the supervisor token
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

    def _register_local_plugins(self) -> None:
        """Auto-discover and register plugins from overblick/plugins/_local/.

        Scans each subdirectory for a plugin.py module containing a PluginBase
        subclass and registers it with the plugin registry. This allows local
        (git-ignored) plugins to be loaded without modifying tracked files.
        """
        local_dir = Path(__file__).parent.parent / "plugins" / "_local"
        if not local_dir.is_dir():
            return

        for candidate in sorted(local_dir.iterdir()):
            plugin_file = candidate / "plugin.py"
            if not candidate.is_dir() or not plugin_file.exists():
                continue

            module_path = f"overblick.plugins._local.{candidate.name}.plugin"
            try:
                mod = importlib.import_module(module_path)
            except Exception as e:
                logger.warning("Failed to import local plugin '%s': %s", candidate.name, e)
                continue

            # Find the PluginBase subclass in the module
            cls_name = None
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, PluginBase)
                    and attr is not PluginBase
                ):
                    cls_name = attr_name
                    break

            if cls_name:
                self._registry.register(candidate.name, module_path, cls_name)
                logger.info(
                    "Local plugin registered: %s -> %s.%s",
                    candidate.name,
                    module_path,
                    cls_name,
                )

    def _load_local_plugin_config(self) -> list[str]:
        """Read local plugin names for the current identity from config/overblick.yaml.

        Expected YAML structure::

            local_plugins:
              <identity>:
                - <plugin_name>

        Returns:
            List of local plugin names to load for this identity.
        """
        config_path = self._base_dir / "config" / "overblick.yaml"
        if not config_path.exists():
            return []

        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            local_cfg = cfg.get("local_plugins", {})
            plugins = local_cfg.get(self._identity_name, [])
            if plugins:
                logger.info(
                    "Local plugins for '%s': %s",
                    self._identity_name,
                    plugins,
                )
            return list(plugins)
        except Exception as e:
            logger.warning("Failed to read local plugin config: %s", e)
            return []

    def _create_output_safety(self) -> OutputSafety | None:
        """Create output safety filter from identity config."""
        if not self._identity.security.enable_output_safety:
            logger.info("Output safety disabled by identity config")
            return None

        # Get banned slang and replacements from personality
        personality = self._identity.personality
        banned_slang = []
        slang_replacements = {}
        if personality:
            vocab = personality.get("vocabulary", {})
            banned_slang = [rf"\b{w}\b" for w in vocab.get("banned_words", [])]
            slang_replacements = vocab.get("slang_replacements", {})

        deflections = self._identity.deflections
        deflection_list = deflections if isinstance(deflections, list) else []

        return OutputSafety(
            identity_name=self._identity_name,
            banned_slang_patterns=banned_slang,
            slang_replacements=slang_replacements,
            deflections=deflection_list if deflection_list else None,
        )

    def _resolve_plugin_dependencies(self, plugin_names: List[str]) -> List[str]:
        """
        Topologically sort plugin names based on DEPENDS_ON declarations.

        Args:
            plugin_names: List of plugin names to load

        Returns:
            Sorted list respecting dependencies (dependencies first)

        Raises:
            RuntimeError: If circular dependency detected
        """
        from collections import deque

        # Build adjacency list: dependency -> dependents
        graph: Dict[str, Set[str]] = {name: set() for name in plugin_names}
        # Track dependencies that are not in plugin_names (external or optional)
        external_deps = []

        for name in plugin_names:
            try:
                metadata = self._registry.get_plugin_metadata(name)
                deps = metadata.depends_on
                for dep in deps:
                    if dep in plugin_names:
                        # Edge from dependency to dependent: dep -> name
                        graph[dep].add(name)
                    else:
                        external_deps.append(dep)
            except Exception as e:
                logger.warning(f"Failed to read dependencies for plugin {name}: {e}")

        if external_deps:
            logger.debug(f"External dependencies not in plugin list: {external_deps}")

        # Kahn's algorithm
        # Compute indegree (number of incoming edges)
        indegree: Dict[str, int] = {name: 0 for name in plugin_names}
        for name in plugin_names:
            for dependent in graph[name]:
                indegree[dependent] += 1

        # Initialize queue with nodes having indegree 0 (no dependencies)
        queue = deque([name for name in plugin_names if indegree[name] == 0])
        sorted_list = []

        while queue:
            node = queue.popleft()
            sorted_list.append(node)
            # For each dependent of node, decrement indegree
            for dependent in graph[node]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        # Check for cycles
        if len(sorted_list) != len(plugin_names):
            # Find nodes still with indegree > 0 (cycle)
            remaining = [name for name in plugin_names if indegree[name] > 0]
            raise RuntimeError(f"Circular dependency detected among plugins: {remaining}")

        return sorted_list
