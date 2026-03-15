"""
Orchestrator bootstrap — setup logic for manual (non‑factory) initialization.
"""

import logging
from pathlib import Path
from typing import Any

from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.orchestrator_bootstrap_result import OrchestratorBootstrapResult
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager
from overblick.identities import load_identity

logger = logging.getLogger(__name__)


class OrchestratorBootstrap:
    """Handles manual (non‑factory) setup of orchestrator components."""

    def __init__(
        self,
        *,
        identity_name: str,
        base_dir: Path,
        plugin_names: list[str],
        services: OrchestratorServices,
        runtime_state: OrchestratorRuntimeState,
        factory: Any = None,
    ) -> None:
        """
        Args:
            identity_name: Name of the identity to load
            base_dir: Root directory of the project
            plugin_names: List of plugins requested via constructor or config
            services: Services container to populate
            runtime_state: Runtime state container to populate
            factory: Optional component factory for dependency injection
        """
        self._identity_name = identity_name
        self._base_dir = base_dir
        self._plugin_names = plugin_names
        self._services = services
        self._runtime_state = runtime_state
        self._factory = factory

    async def setup(self) -> OrchestratorBootstrapResult:
        """Perform full manual setup (equivalent to else branch in original setup)."""
        logger.info("Starting manual bootstrap for identity: %s", self._identity_name)

        # 1. Load identity
        self._services.identity = load_identity(self._identity_name)
        logger.info(
            "Identity loaded: %s v%s",
            self._services.identity.display_name,
            self._services.identity.version,
        )

        # 2. Create paths
        paths = OrchestratorPaths.for_identity(self._base_dir, self._identity_name)
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.log_dir.mkdir(parents=True, exist_ok=True)

        # 3. Initialize security
        self._services.secrets = SecretsManager(paths.secrets_dir)
        self._services.audit_log = AuditLog(paths.data_dir / "audit.db", self._identity_name)
        self._services.audit_log.log("orchestrator_setup", category="lifecycle")

        # 3b. Initialize engagement database (lazy — only if moltbook is active)
        plugin_names = (
            list(self._services.identity.plugins) if self._services.identity.plugins else []
        )
        if "moltbook" in plugin_names:
            eng_db_config = DatabaseConfig(
                sqlite_path=str(paths.data_dir / "engagement.db"),
            )
            self._services.engagement_db_backend = SQLiteBackend(
                eng_db_config, identity=self._identity_name
            )
            await self._services.engagement_db_backend.connect()
            self._services.engagement_db = EngagementDB(
                self._services.engagement_db_backend, identity=self._identity_name
            )
            await self._services.engagement_db.setup()
            logger.info("EngagementDB initialized for %s", self._identity_name)
        else:
            logger.debug("EngagementDB skipped — no moltbook plugin for %s", self._identity_name)

        # 4. Initialize quiet hours
        self._services.quiet_hours = QuietHoursChecker(self._services.identity.quiet_hours)

        # 5. Initialize LLM client
        self._services.llm_client = await self._create_llm_client()

        # 6. Initialize security subsystems
        self._services.preflight = self._create_preflight()
        self._services.output_safety = self._create_output_safety()
        self._services.rate_limiter = RateLimiter(
            max_tokens=self._services.identity.security.rate_limiter_max_tokens,
            refill_rate=self._services.identity.security.rate_limiter_refill_rate,
        )

        # 7. Create safe LLM pipeline
        self._services.llm_pipeline = SafeLLMPipeline(
            llm_client=self._services.llm_client,
            audit_log=self._services.audit_log,
            preflight_checker=self._services.preflight,
            output_safety=self._services.output_safety,
            rate_limiter=self._services.rate_limiter,
            identity_name=self._identity_name,
            strict=True,  # Main agent pipeline uses full security
        )
        logger.info("SafeLLMPipeline initialized with full security chain")

        # 8. Create permissions and capability checker for plugin loading
        self._services.permissions = PermissionChecker.from_identity(self._services.identity)
        self._services.capability_checker = PluginCapabilityChecker(
            identity_name=self._identity_name,
            raw_config=self._services.identity.raw_config,
        )

        # 9. Create shared capabilities (orchestrator-level)
        await self._setup_capabilities()

        # 10. Initialize per-identity learning store
        await self._setup_learning_store(paths.data_dir)

        # 11. Create IPC client (if running under supervisor)
        if self._services.ipc_client is None:
            self._services.ipc_client = self._create_ipc_client()

        logger.info("Manual bootstrap complete")

        return OrchestratorBootstrapResult(
            services=self._services,
            runtime_state=self._runtime_state,
            paths=paths,
        )

    # --- Helper methods (copied from orchestrator.py) ---
    async def _create_llm_client(self) -> Any:
        # This is a stub — real implementation lives in orchestrator.py
        # We keep it here for compatibility during transition
        raise NotImplementedError("_create_llm_client must be implemented by Orchestrator")

    def _create_preflight(self) -> Any:
        # This is a stub — real implementation lives in orchestrator.py
        raise NotImplementedError("_create_preflight must be implemented by Orchestrator")

    def _create_output_safety(self) -> Any:
        # This is a stub — real implementation lives in orchestrator.py
        raise NotImplementedError("_create_output_safety must be implemented by Orchestrator")

    def _create_ipc_client(self) -> Any:
        # This is a stub — real implementation lives in orchestrator.py
        raise NotImplementedError("_create_ipc_client must be implemented by Orchestrator")

    async def _setup_capabilities(self) -> None:
        # This is a stub — real implementation lives in orchestrator.py
        raise NotImplementedError("_setup_capabilities must be implemented by Orchestrator")

    async def _setup_learning_store(self, data_dir: Path) -> None:
        # This is a stub — real implementation lives in orchestrator.py
        raise NotImplementedError("_setup_learning_store must be implemented by Orchestrator")
