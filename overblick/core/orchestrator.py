"""
Orchestrator — agent lifecycle manager.

Manages the full lifecycle: INIT -> SETUP -> RUNNING -> STOP.
Wires together identity, plugins, LLM, security, and scheduling.
"""

import asyncio
import logging
import signal
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.event_bus import EventBus
from overblick.personalities import Personality as Identity, load_personality as load_identity
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler
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

        # 3. Initialize security
        self._secrets = SecretsManager(secrets_dir)
        self._audit_log = AuditLog(data_dir / "audit.db", self._identity_name)
        self._audit_log.log("orchestrator_setup", category="lifecycle")

        # 4. Initialize quiet hours
        self._quiet_hours = QuietHoursChecker(self._identity.quiet_hours)

        # 5. Initialize LLM client
        self._llm_client = await self._create_llm_client()

        # 6. Initialize security subsystems
        self._preflight = self._create_preflight()
        self._output_safety = self._create_output_safety()
        self._rate_limiter = RateLimiter(max_tokens=10, refill_rate=0.5)

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

        # 9. Load and setup plugins/connectors
        permissions = PermissionChecker.from_identity(self._identity)

        # Use connectors from identity if specified, otherwise fall back to constructor arg
        connector_names = list(self._identity.connectors) if self._identity.connectors else self._plugin_names

        for plugin_name in connector_names:
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
            # Register plugin ticks in scheduler
            for plugin in self._plugins:
                interval = self._identity.schedule.feed_poll_minutes * 60
                self._scheduler.add(
                    f"tick_{plugin.name}",
                    plugin.tick,
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
                logger.error(f"Error tearing down '{plugin.name}': {e}")

        # Close LLM client
        if self._llm_client and hasattr(self._llm_client, "close"):
            try:
                await self._llm_client.close()
            except Exception as e:
                logger.error(f"Error closing LLM client: {e}")

        # Final audit log
        if self._audit_log:
            self._audit_log.log("orchestrator_stopped", category="lifecycle")
            self._audit_log.close()

        # Cleanup event bus
        self._event_bus.clear()

        self._state = OrchestratorState.STOPPED
        logger.info("Orchestrator stopped cleanly")

    async def _setup_capabilities(self) -> None:
        """Create shared capabilities at the orchestrator level."""
        # Determine which capabilities to create
        cap_names = list(self._identity.capability_names) if self._identity.capability_names else []

        # Fall back to enabled_modules if no explicit capabilities
        if not cap_names and self._identity.enabled_modules:
            cap_names = list(self._identity.enabled_modules)

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

        When use_gateway=True, uses GatewayClient which routes through the
        priority queue (HIGH for interactive, LOW for background). Otherwise
        connects directly to Ollama.
        """
        llm_cfg = self._identity.llm

        if llm_cfg.use_gateway:
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
        else:
            from overblick.core.llm.ollama_client import OllamaClient

            client = OllamaClient(
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                top_p=llm_cfg.top_p,
                max_tokens=llm_cfg.max_tokens,
                timeout_seconds=llm_cfg.timeout_seconds,
            )

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
