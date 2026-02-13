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

from blick.core.event_bus import EventBus
from blick.core.identity import Identity, load_identity
from blick.core.llm.pipeline import SafeLLMPipeline
from blick.core.permissions import PermissionChecker
from blick.core.plugin_base import PluginBase, PluginContext
from blick.core.plugin_registry import PluginRegistry
from blick.core.quiet_hours import QuietHoursChecker
from blick.core.scheduler import Scheduler
from blick.core.security.audit_log import AuditLog
from blick.core.security.output_safety import OutputSafety
from blick.core.security.preflight import PreflightChecker
from blick.core.security.rate_limiter import RateLimiter
from blick.core.security.secrets_manager import SecretsManager

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

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def identity(self) -> Optional[Identity]:
        return self._identity

    async def setup(self) -> None:
        """Initialize all framework components and plugins."""
        self._state = OrchestratorState.SETUP
        logger.info(f"Setting up Blick orchestrator for identity: {self._identity_name}")

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

        # 8. Load and setup plugins
        permissions = PermissionChecker.from_identity(self._identity)

        for plugin_name in self._plugin_names:
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

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        self._audit_log.log("orchestrator_started", category="lifecycle")
        logger.info(f"Blick orchestrator running as '{self._identity.display_name}'")

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

            # Run scheduler (blocks until stop)
            await self._scheduler.start()

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

    async def _create_llm_client(self) -> object:
        """Create the appropriate LLM client based on identity config."""
        from blick.core.llm.ollama_client import OllamaClient

        client = OllamaClient(
            model=self._identity.llm.model,
            temperature=self._identity.llm.temperature,
            top_p=self._identity.llm.top_p,
            max_tokens=self._identity.llm.max_tokens,
            timeout_seconds=self._identity.llm.timeout_seconds,
        )

        # Health check
        if await client.health_check():
            logger.info(f"LLM client ready: {self._identity.llm.model}")
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
