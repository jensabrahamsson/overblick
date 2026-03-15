"""
Orchestrator bootstrap — setup logic for manual (non‑factory) initialization.
"""

import logging
from typing import Any

from overblick.core.database import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.llm.pipeline import SafeLLMPipeline
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

    def __init__(self, orchestrator: Any):
        """
        Args:
            orchestrator: The Orchestrator instance whose components will be set up.
                          Must have attributes: _identity_name, _base_dir, _plugin_names,
                          _identity, _secrets, _audit_log, etc.
        """
        self._orch = orchestrator
        self._identity_name = orchestrator._identity_name
        self._base_dir = orchestrator._base_dir
        self._plugin_names = orchestrator._plugin_names

    async def setup(self) -> None:
        """Perform full manual setup (equivalent to else branch in original setup)."""
        logger.info("Starting manual bootstrap for identity: %s", self._identity_name)

        # 1. Load identity
        self._orch._identity = load_identity(self._identity_name)
        logger.info(
            "Identity loaded: %s v%s",
            self._orch._identity.display_name,
            self._orch._identity.version,
        )

        # 2. Create paths
        data_dir = self._base_dir / "data" / self._identity_name
        log_dir = self._base_dir / "logs" / self._identity_name
        secrets_dir = self._base_dir / "config" / "secrets"

        data_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        self._orch._control_file = data_dir / "plugin_control.json"

        # 3. Initialize security
        self._orch._secrets = SecretsManager(secrets_dir)
        self._orch._audit_log = AuditLog(data_dir / "audit.db", self._identity_name)
        self._orch._audit_log.log("orchestrator_setup", category="lifecycle")

        # 3b. Initialize engagement database (lazy — only if moltbook is active)
        plugin_names = list(self._orch._identity.plugins) if self._orch._identity.plugins else []
        if "moltbook" in plugin_names:
            eng_db_config = DatabaseConfig(
                sqlite_path=str(data_dir / "engagement.db"),
            )
            self._orch._engagement_db_backend = SQLiteBackend(
                eng_db_config, identity=self._identity_name
            )
            await self._orch._engagement_db_backend.connect()
            self._orch._engagement_db = EngagementDB(
                self._orch._engagement_db_backend, identity=self._identity_name
            )
            await self._orch._engagement_db.setup()
            logger.info("EngagementDB initialized for %s", self._identity_name)
        else:
            logger.debug("EngagementDB skipped — no moltbook plugin for %s", self._identity_name)

        # 4. Initialize quiet hours
        self._orch._quiet_hours = QuietHoursChecker(self._orch._identity.quiet_hours)

        # 5. Initialize LLM client
        self._orch._llm_client = await self._orch._create_llm_client()

        # 6. Initialize security subsystems
        self._orch._preflight = self._orch._create_preflight()
        self._orch._output_safety = self._orch._create_output_safety()
        self._orch._rate_limiter = RateLimiter(
            max_tokens=self._orch._identity.security.rate_limiter_max_tokens,
            refill_rate=self._orch._identity.security.rate_limiter_refill_rate,
        )

        # 7. Create safe LLM pipeline
        self._orch._llm_pipeline = SafeLLMPipeline(
            llm_client=self._orch._llm_client,
            audit_log=self._orch._audit_log,
            preflight_checker=self._orch._preflight,
            output_safety=self._orch._output_safety,
            rate_limiter=self._orch._rate_limiter,
            identity_name=self._identity_name,
            strict=True,  # Main agent pipeline uses full security
        )
        logger.info("SafeLLMPipeline initialized with full security chain")

        # 8. Create permissions and capability checker for plugin loading
        PermissionChecker.from_identity(self._orch._identity)
        PluginCapabilityChecker(
            identity_name=self._identity_name,
            raw_config=self._orch._identity.raw_config,
        )

        # 9. Create shared capabilities (orchestrator-level)
        await self._orch._setup_capabilities()

        # 10. Initialize per-identity learning store
        await self._orch._setup_learning_store(data_dir)

        # 11. Create IPC client (if running under supervisor)
        if self._orch._ipc_client is None:
            self._orch._ipc_client = self._orch._create_ipc_client()

        logger.info("Manual bootstrap complete")
