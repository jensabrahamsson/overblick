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


# Use the imported OrchestratorState from orchestrator_types
# class OrchestratorState(Enum):
#     """Orchestrator lifecycle states."""
#
#     INIT = "init"
#     SETUP = "setup"
#     RUNNING = "running"
#     STOPPING = "stopping"
#     STOPPED = "stopped"


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

        # Framework components (initialized in setup)
        self._identity: Identity | None = None
        self._event_bus: EventBus = EventBus()
        self._scheduler: Scheduler = Scheduler()
        self._registry: PluginRegistry = PluginRegistry()
        self._audit_log: AuditLog | None = None
        self._secrets: SecretsManager | None = None
        self._quiet_hours: QuietHoursChecker | None = None
        self._llm_client: LLMClient | None = None
        self._llm_pipeline: SafeLLMPipeline | None = None
        self._preflight: PreflightChecker | None = None
        self._output_safety: OutputSafety | None = None
        self._rate_limiter: RateLimiter | None = None
        self._permissions: PermissionChecker | None = None
        self._capability_checker: PluginCapabilityChecker | None = None
        self._policy_gate: PolicyGate | None = None
        self._plugins: list[PluginBase] = []
        self._capabilities: dict[str, CapabilityBase] = {}
        self._capabilities_setup = False
        self._ipc_client: IPCClient | None = None
        self._engagement_db_backend: SQLiteBackend | None = None
        self._engagement_db: EngagementDB | None = None
        self._learning_store: LearningStore | None = None
        self._control_file: Path | None = None
        self._control_cache: dict[str, str] = {}
        self._control_cache_ts: float = 0.0

        # New modular components
        self._bootstrap: OrchestratorBootstrap | None = None
        self._runtime: OrchestratorRuntime | None = None
        self._shutdown: OrchestratorShutdown | None = None

        # Register local plugins after registry is created
        self._register_local_plugins()

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def identity(self) -> Identity | None:
        return self._identity

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
            policy_gate=self._policy_gate,
        )
        # Set secrets getter (required for ctx.get_secret)
        ctx._secrets_getter = lambda key: self._secrets.get(self._identity_name, key)  # type: ignore[union-attr]
        # Set raw LLM client (access controlled via property)
        ctx._llm_client = self._llm_client
        # Ensure bundles are populated
        ctx._ensure_bundles()
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
            self._permissions = permissions
            capability_checker = factory_components["capability_checker"]
            self._capability_checker = capability_checker
            self._policy_gate = factory_components["policy_gate"]
            self._ipc_client = factory_components["ipc_client"]  # type: ignore
            self._event_bus = factory_components["event_bus"]
            self._scheduler = factory_components["scheduler"]
            self._registry = factory_components["registry"]

            data_dir = paths["data_dir"]
            log_dir = paths["log_dir"]
            secrets_dir = paths["secrets_dir"]
        else:
            # Use the new bootstrap module
            self._bootstrap = OrchestratorBootstrap(self)
            await self._bootstrap.setup()
            # After bootstrap, the orchestrator's components are set up.
            assert self._identity is not None, "Identity must be loaded by bootstrap"
            # Retrieve paths and other variables from orchestrator's state.
            data_dir = self._base_dir / "data" / self._identity_name
            log_dir = self._base_dir / "logs" / self._identity_name
            secrets_dir = self._base_dir / "config" / "secrets"
            permissions = PermissionChecker.from_identity(self._identity)
            self._permissions = permissions
            capability_checker = PluginCapabilityChecker(
                identity_name=self._identity_name,
                raw_config=self._identity.raw_config,
            )
            self._capability_checker = capability_checker

        assert data_dir is not None and log_dir is not None and secrets_dir is not None, (
            "Paths must be defined"
        )
        assert self._identity is not None, "Identity must be loaded by this point"
        # 2b. Plugin control file (per-agent stop/start from dashboard)"
        self._control_file = data_dir / "plugin_control.json"

        if not use_factory:
            # Bootstrap already handled everything else, but we need to ensure
            # permissions and capability_checker are set (they are already set above).
            pass

        # Ensure required variables are set
        assert data_dir is not None and log_dir is not None, "Paths must be defined"
        assert permissions is not None, "Permissions must be defined"
        assert capability_checker is not None, "Capability checker must be defined"

        # Create centralized policy gate
        self._policy_gate = self._create_policy_gate()

        # 9. Create shared capabilities (orchestrator-level)
        if use_factory:
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

        assert self._audit_log is not None, "Audit log must be initialized before loading plugins"
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

        # Delegate to runtime module
        self._runtime = OrchestratorRuntime(self)
        await self._runtime.run()

    async def stop(self) -> None:
        """Gracefully stop everything."""
        if self._state == OrchestratorState.STOPPING:
            return  # Prevent double-stop

        # Delegate to shutdown module
        if self._shutdown is None:
            self._shutdown = OrchestratorShutdown(self)
        await self._shutdown.shutdown()

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

        assert self._identity is not None, (
            "Identity must be loaded before setting up learning store"
        )

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
            # self._llm_client is not None at this point
            llm_client = self._llm_client

            async def _embed(text: str) -> list[float]:
                return await llm_client.embed(text)  # type: ignore[attr-defined]

            return _embed
        return None

    async def _setup_capabilities(self) -> None:
        """Create shared capabilities at the orchestrator level."""
        if self._capabilities_setup:
            return
        assert self._identity is not None, "Identity must be loaded before setting up capabilities"
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
            self._capabilities_setup = True
            return

        try:
            registry = CapabilityRegistry.default()
        except Exception as e:
            logger.warning("Could not load capability registry: %s", e)
            self._capabilities_setup = True
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
            event_bus=self._event_bus,
            audit_log=self._audit_log,
            quiet_hours_checker=self._quiet_hours,
            llm_pipeline=self._llm_pipeline,
            identity=self._identity,
        )
        # Attach secrets getter (capabilities like 'email' need it)
        assert self._secrets is not None, (
            "Secrets manager must be initialized before setting up capabilities"
        )
        secrets = self._secrets
        temp_ctx._secrets_getter = lambda key, _id=self._identity_name: secrets.get(_id, key)

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

        self._capabilities_setup = True
        logger.info("Orchestrator created %d shared capabilities", len(self._capabilities))

    async def _create_llm_client(self) -> LLMClient:
        """Create LLM client — all agents route through the LLM Gateway.

        The gateway handles backend routing (local Ollama, cloud Ollama, OpenAI)
        based on its own configuration. Agents only need to know the gateway URL.
        """
        from overblick.core.llm.gateway_client import GatewayClient

        assert self._identity is not None, "Identity must be loaded before creating LLM client"

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
        assert self._identity is not None, (
            "Identity must be loaded before creating preflight checker"
        )
        assert self._llm_client is not None, (
            "LLM client must be initialized before creating preflight checker"
        )
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
        assert self._identity is not None, "Identity must be loaded before creating output safety"
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

    def _create_policy_gate(self) -> PolicyGate:
        """Create centralized policy gate for security enforcement."""
        assert self._identity is not None, "Identity must be loaded before creating policy gate"
        assert self._permissions is not None, "Permissions must be initialized"
        assert self._capability_checker is not None, "Capability checker must be initialized"
        # Note: llm_pipeline, preflight, output_safety, rate_limiter may be None
        return PolicyGate(
            identity_name=self._identity_name,
            permission_checker=self._permissions,
            capability_checker=self._capability_checker,
            llm_pipeline=self._llm_pipeline,
            preflight_checker=self._preflight,
            output_safety=self._output_safety,
            rate_limiter=self._rate_limiter,
        )

    def _resolve_plugin_dependencies(self, plugin_names: list[str]) -> list[str]:
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
        graph: dict[str, set[str]] = {name: set() for name in plugin_names}
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
        indegree: dict[str, int] = dict.fromkeys(plugin_names, 0)
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
