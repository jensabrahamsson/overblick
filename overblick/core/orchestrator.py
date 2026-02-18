"""
Orchestrator — agent lifecycle manager.

Manages the full lifecycle: INIT -> SETUP -> RUNNING -> STOP.
Wires together identity, plugins, LLM, security, and scheduling.
"""

import asyncio
import json
import logging
import signal
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.event_bus import EventBus
from overblick.identities import Identity, load_identity
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler
from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.output_safety import OutputSafety
from overblick.core.security.preflight import PreflightChecker
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager

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

    def __init__(
        self,
        identity_name: str,
        base_dir: Optional[Path] = None,
        plugins: Optional[list[str]] = None,
    ):
        self._identity_name = identity_name
        self._base_dir = base_dir or Path(__file__).parent.parent.parent
        self._plugin_names = plugins or ["moltbook"]
        self._state = OrchestratorState.INIT

        self._shutdown_event = asyncio.Event()

        # Framework components (initialized in setup)
        self._identity: Optional[Identity] = None
        self._event_bus = EventBus()
        self._scheduler = Scheduler()
        self._registry = PluginRegistry()
        self._audit_log: Optional[AuditLog] = None
        self._secrets: Optional[SecretsManager] = None
        self._quiet_hours: Optional[QuietHoursChecker] = None
        self._llm_client: Optional[object] = None
        self._llm_pipeline: Optional[SafeLLMPipeline] = None
        self._preflight: Optional[PreflightChecker] = None
        self._output_safety: Optional[OutputSafety] = None
        self._rate_limiter: Optional[RateLimiter] = None
        self._plugins: list[PluginBase] = []
        self._capabilities: dict[str, CapabilityBase] = {}
        self._ipc_client: Optional[object] = None
        self._engagement_db_backend: Optional[SQLiteBackend] = None
        self._engagement_db: Optional[EngagementDB] = None
        self._control_file: Optional[Path] = None

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def identity(self) -> Optional[Identity]:
        return self._identity

    async def setup(self) -> None:
        """Initialize all framework components and plugins."""
        self._state = OrchestratorState.SETUP
        logger.info(f"Setting up Överblick orchestrator for identity: {self._identity_name}")

        # 1. Load identity
        self._identity = load_identity(self._identity_name)
        logger.info(f"Identity loaded: {self._identity.display_name} v{self._identity.version}")

        # 2. Setup paths
        data_dir = self._base_dir / "data" / self._identity_name
        log_dir = self._base_dir / "logs" / self._identity_name
        secrets_dir = self._base_dir / "config" / "secrets"

        data_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        # 2b. Plugin control file (per-agent stop/start from dashboard)
        self._control_file = data_dir / "plugin_control.json"

        # 3. Initialize security
        self._secrets = SecretsManager(secrets_dir)
        self._audit_log = AuditLog(data_dir / "audit.db", self._identity_name)
        self._audit_log.log("orchestrator_setup", category="lifecycle")

        # 3b. Initialize engagement database
        eng_db_config = DatabaseConfig(
            sqlite_path=str(data_dir / "engagement.db"),
        )
        self._engagement_db_backend = SQLiteBackend(eng_db_config, identity=self._identity_name)
        await self._engagement_db_backend.connect()
        self._engagement_db = EngagementDB(self._engagement_db_backend, identity=self._identity_name)
        await self._engagement_db.setup()
        logger.info("EngagementDB initialized for %s", self._identity_name)

        # 4. Initialize quiet hours
        self._quiet_hours = QuietHoursChecker(self._identity.quiet_hours)

        # 5. Initialize LLM client
        self._llm_client = await self._create_llm_client()

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
        )
        logger.info("SafeLLMPipeline initialized with full security chain")

        # 8. Create shared capabilities (orchestrator-level)
        await self._setup_capabilities()

        # 9. Create IPC client (if running under supervisor)
        self._ipc_client = self._create_ipc_client()

        # 10. Load and setup plugins
        permissions = PermissionChecker.from_identity(self._identity)

        # Use plugins from identity if specified, otherwise fall back to constructor arg
        plugin_names = list(self._identity.plugins) if self._identity.plugins else self._plugin_names

        for plugin_name in plugin_names:
            ctx = PluginContext(
                identity_name=self._identity_name,
                data_dir=data_dir / plugin_name,
                log_dir=log_dir,
                llm_client=self._llm_client,
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
            )
            ctx._secrets_getter = lambda key, _id=self._identity_name: self._secrets.get(_id, key)

            try:
                plugin = self._registry.load(plugin_name, ctx)
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
            raise RuntimeError("No plugins loaded — cannot start")

        logger.info(f"Setup complete: {len(self._plugins)} plugin(s) active")

    async def run(self) -> None:
        """
        Main run loop. Blocks until shutdown signal.
        """
        await self.setup()
        self._state = OrchestratorState.RUNNING

        # Register signal handlers — only set a flag (signal-safe)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        self._audit_log.log("orchestrator_started", category="lifecycle")
        logger.info(f"Överblick orchestrator running as '{self._identity.display_name}'")

        try:
            # Register plugin ticks in scheduler (guarded by control file)
            for plugin in self._plugins:
                interval = self._identity.schedule.feed_poll_minutes * 60

                async def _guarded_tick(p=plugin):
                    if await self._is_plugin_stopped(p.name):
                        logger.debug("Agent '%s' stopped via control file, skipping tick", p.name)
                        return
                    await p.tick()

                self._scheduler.add(
                    f"tick_{plugin.name}",
                    _guarded_tick,
                    interval_seconds=interval,
                    run_immediately=True,
                )

            # Run scheduler and shutdown event concurrently — first to complete wins
            scheduler_task = asyncio.create_task(self._scheduler.start())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())

            done, pending = await asyncio.wait(
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
        """Check if a plugin is stopped via the dashboard control file."""
        if not self._control_file:
            return False
        try:
            if await asyncio.to_thread(self._control_file.exists):
                text = await asyncio.to_thread(self._control_file.read_text)
                data = json.loads(text)
                return data.get(plugin_name) == "stopped"
        except Exception as e:
            logger.debug("Could not read plugin control file: %s", e)
        return False

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

        # Build per-capability configs from identity
        system_prompt = f"You are {self._identity.display_name}."
        configs = {
            "dream_system": {
                "dream_templates": self._identity.raw_config.get("dream_templates"),
            },
            "therapy_system": {
                "therapy_day": self._identity.raw_config.get("therapy_day", 6),
                "system_prompt": system_prompt,
            },
            "safe_learning": {
                "ethos_text": self._identity.raw_config.get("ethos_text", ""),
            },
            "emotional_state": {},
            "analyzer": {
                "interest_keywords": self._identity.interest_keywords,
                "engagement_threshold": self._identity.engagement_threshold,
                "agent_name": self._identity.raw_config.get("agent_name", self._identity.name),
            },
            "composer": {
                "system_prompt": system_prompt,
                "temperature": self._identity.llm.temperature,
                "max_tokens": self._identity.llm.max_tokens,
            },
            "conversation_tracker": {},
            "summarizer": {},
        }

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

    async def _create_llm_client(self) -> object:
        """Create the appropriate LLM client based on identity config.

        Routes to one of three backends based on llm.provider:
        - "ollama": Direct local Ollama connection
        - "gateway": LLM Gateway with priority queue
        - "cloud": Cloud LLM provider (stub — not yet implemented)
        """
        llm_cfg = self._identity.llm

        match llm_cfg.provider:
            case "ollama":
                from overblick.core.llm.ollama_client import OllamaClient

                client = OllamaClient(
                    model=llm_cfg.model,
                    temperature=llm_cfg.temperature,
                    top_p=llm_cfg.top_p,
                    max_tokens=llm_cfg.max_tokens,
                    timeout_seconds=llm_cfg.timeout_seconds,
                )

            case "gateway":
                from overblick.core.llm.gateway_client import GatewayClient

                client = GatewayClient(
                    base_url=llm_cfg.gateway_url,
                    model=llm_cfg.model,
                    default_priority="low",
                    max_tokens=llm_cfg.max_tokens,
                    temperature=llm_cfg.temperature,
                    top_p=llm_cfg.top_p,
                    timeout_seconds=llm_cfg.timeout_seconds,
                )
                logger.info("Using LLM Gateway at %s (priority queue enabled)", llm_cfg.gateway_url)

            case "cloud":
                from overblick.core.llm.cloud_client import CloudLLMClient

                # Load API key from secrets manager
                api_key = ""
                if self._secrets:
                    api_key = self._secrets.get(
                        self._identity_name, llm_cfg.cloud_secret_key
                    ) or ""

                client = CloudLLMClient(
                    api_url=llm_cfg.cloud_api_url,
                    model=llm_cfg.cloud_model or llm_cfg.model,
                    api_key=api_key,
                    temperature=llm_cfg.temperature,
                    max_tokens=llm_cfg.max_tokens,
                    top_p=llm_cfg.top_p,
                    timeout_seconds=llm_cfg.timeout_seconds,
                )
                logger.info(
                    "Using Cloud LLM provider at %s (model: %s)",
                    llm_cfg.cloud_api_url,
                    llm_cfg.cloud_model or llm_cfg.model,
                )

            case _:
                raise ValueError(f"Unknown LLM provider: {llm_cfg.provider}")

        # Health check
        if await client.health_check():
            logger.info("LLM client ready: %s", llm_cfg.model)
        else:
            logger.warning("LLM health check failed — agent may have limited functionality")

        return client

    def _create_preflight(self) -> Optional[PreflightChecker]:
        """Create preflight checker from identity security config."""
        if not self._identity.security.enable_preflight:
            logger.info("Preflight checker disabled by identity config")
            return None

        admin_ids = set(self._identity.security.admin_user_ids)
        deflections = self._identity.deflections if isinstance(self._identity.deflections, dict) else {}

        return PreflightChecker(
            llm_client=self._llm_client,
            admin_user_ids=admin_ids,
            deflections=deflections,
        )

    def _create_ipc_client(self) -> Optional[object]:
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
            auth_token = token_path.read_text().strip()
            from overblick.supervisor.ipc import IPCClient

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

    def _create_output_safety(self) -> Optional[OutputSafety]:
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
