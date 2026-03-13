"""
Component factory for Orchestrator — dependency injection for testability.

Extracts component creation logic from Orchestrator.setup() into a dedicated
factory class. This reduces coupling and makes testing individual subsystems
easier.

Usage:
    factory = ComponentFactory(identity_name="anomal", base_dir=Path("."))
    identity = await factory.load_identity()
    secrets = factory.create_secrets_manager()
    # ... etc
"""

import logging
from pathlib import Path
from typing import Any

from overblick.core.capability import CapabilityRegistry
from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.event_bus import EventBus
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.output_safety import OutputSafety
from overblick.core.security.preflight import PreflightChecker
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager
from overblick.identities import Identity, load_identity

logger = logging.getLogger(__name__)


class ComponentFactory:
    """Factory for creating orchestrator components with dependency injection."""

    def __init__(self, identity_name: str, base_dir: Path):
        self._identity_name = identity_name
        self._base_dir = base_dir

        # Component cache
        self._identity: Identity | None = None
        self._paths: dict[str, Path] | None = None

    # ---- Core component factories ----

    async def load_identity(self) -> Identity:
        """Load identity configuration."""
        if self._identity is None:
            self._identity = load_identity(self._identity_name)
            logger.info(f"Identity loaded: {self._identity.display_name} v{self._identity.version}")
        return self._identity

    def get_paths(self) -> dict[str, Path]:
        """Get standard directory paths for this identity."""
        if self._paths is None:
            data_dir = self._base_dir / "data" / self._identity_name
            log_dir = self._base_dir / "logs" / self._identity_name
            secrets_dir = self._base_dir / "config" / "secrets"

            data_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)

            self._paths = {
                "data_dir": data_dir,
                "log_dir": log_dir,
                "secrets_dir": secrets_dir,
            }
        return self._paths

    def create_secrets_manager(self) -> SecretsManager:
        """Create secrets manager."""
        paths = self.get_paths()
        return SecretsManager(paths["secrets_dir"])

    def create_audit_log(self) -> AuditLog:
        """Create audit log."""
        paths = self.get_paths()
        return AuditLog(paths["data_dir"] / "audit.db", self._identity_name)

    async def create_engagement_db(self, identity: Identity) -> EngagementDB | None:
        """Create engagement database if moltbook plugin is active."""
        plugin_names = list(identity.plugins) if identity.plugins else []
        if "moltbook" not in plugin_names:
            logger.debug("EngagementDB skipped — no moltbook plugin for %s", self._identity_name)
            return None

        paths = self.get_paths()
        eng_db_config = DatabaseConfig(
            sqlite_path=str(paths["data_dir"] / "engagement.db"),
        )
        backend = SQLiteBackend(eng_db_config, identity=self._identity_name)
        await backend.connect()
        engagement_db = EngagementDB(backend, identity=self._identity_name)
        await engagement_db.setup()
        logger.info("EngagementDB initialized for %s", self._identity_name)
        return engagement_db

    def create_quiet_hours_checker(self, identity: Identity) -> QuietHoursChecker:
        """Create quiet hours checker."""
        return QuietHoursChecker(identity.quiet_hours)

    async def create_llm_client(self, identity: Identity) -> Any:
        """Create LLM client — all agents route through the LLM Gateway."""
        from overblick.core.llm.gateway_client import GatewayClient

        llm_cfg = identity.llm
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

    def create_preflight_checker(
        self, identity: Identity, llm_client: Any
    ) -> PreflightChecker | None:
        """Create preflight checker from identity security config."""
        if not identity.security.enable_preflight:
            logger.info("Preflight checker disabled by identity config")
            return None

        admin_ids = set(identity.security.admin_user_ids)
        deflections = identity.deflections if isinstance(identity.deflections, dict) else {}

        return PreflightChecker(
            llm_client=llm_client,
            admin_user_ids=admin_ids,
            deflections=deflections,
        )

    def create_output_safety(self, identity: Identity) -> OutputSafety | None:
        """Create output safety filter from identity config."""
        if not identity.security.enable_output_safety:
            logger.info("Output safety disabled by identity config")
            return None

        # Get banned slang and replacements from personality
        personality = identity.personality
        banned_slang = []
        slang_replacements = {}
        if personality:
            vocab = personality.get("vocabulary", {})
            banned_slang = [rf"\b{w}\b" for w in vocab.get("banned_words", [])]
            slang_replacements = vocab.get("slang_replacements", {})

        deflections = identity.deflections
        deflection_list = deflections if isinstance(deflections, list) else []

        return OutputSafety(
            identity_name=self._identity_name,
            banned_slang_patterns=banned_slang,
            slang_replacements=slang_replacements,
            deflections=deflection_list if deflection_list else None,
        )

    def create_rate_limiter(self, identity: Identity) -> RateLimiter:
        """Create rate limiter."""
        return RateLimiter(
            max_tokens=identity.security.rate_limiter_max_tokens,
            refill_rate=identity.security.rate_limiter_refill_rate,
        )

    def create_safe_llm_pipeline(
        self,
        llm_client: Any,
        audit_log: AuditLog,
        preflight_checker: PreflightChecker | None,
        output_safety: OutputSafety | None,
        rate_limiter: RateLimiter,
        identity: Identity,
    ) -> SafeLLMPipeline:
        """Create safe LLM pipeline."""
        pipeline = SafeLLMPipeline(
            llm_client=llm_client,
            audit_log=audit_log,
            preflight_checker=preflight_checker,
            output_safety=output_safety,
            rate_limiter=rate_limiter,
            identity_name=self._identity_name,
            strict=True,  # Main agent pipeline uses full security
        )
        logger.info("SafeLLMPipeline initialized with full security chain")
        return pipeline

    def create_permission_checker(self, identity: Identity) -> PermissionChecker:
        """Create permission checker."""
        return PermissionChecker.from_identity(identity)

    def create_plugin_capability_checker(self, identity: Identity) -> PluginCapabilityChecker:
        """Create plugin capability checker."""
        return PluginCapabilityChecker(
            identity_name=self._identity_name,
            raw_config=identity.raw_config,
        )

    def create_ipc_client(self) -> Any | None:
        """
        Create an IPC client if a supervisor token file exists.

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
        for dir_path in search_dirs:
            token_path = dir_path / token_name
            if token_path.exists():
                logger.info("Found supervisor token at %s", token_path)
                from overblick.supervisor.ipc import IPCClient

                return IPCClient(
                    token_path=token_path,
                    identity=self._identity_name,
                )

        logger.debug("No supervisor token found — running standalone")
        return None

    # ---- Framework core components (stateless) ----

    def create_event_bus(self) -> EventBus:
        """Create event bus."""
        return EventBus()

    def create_scheduler(self) -> Scheduler:
        """Create scheduler."""
        return Scheduler()

    def create_plugin_registry(self) -> PluginRegistry:
        """Create plugin registry."""
        registry = PluginRegistry()
        # Register local plugins
        self._register_local_plugins(registry)
        return registry

    def create_capability_registry(self) -> CapabilityRegistry:
        """Create capability registry."""
        return CapabilityRegistry()

    # ---- Private helpers ----

    def _register_local_plugins(self, registry: PluginRegistry) -> None:
        """Register plugins from the _local directory (gitignored)."""
        local_dir = self._base_dir / "overblick" / "plugins" / "_local"
        if not local_dir.exists():
            return

        for plugin_dir in local_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            plugin_name = plugin_dir.name
            module_path = f"overblick.plugins._local.{plugin_name}.plugin"

            try:
                registry.register_local(plugin_name, module_path)
                logger.debug("Registered local plugin: %s", plugin_name)
            except Exception as e:
                logger.warning("Failed to register local plugin %s: %s", plugin_name, e)
