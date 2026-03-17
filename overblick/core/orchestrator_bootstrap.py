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
from overblick.core.resource_setup import ResourceSetup
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

        # 8b. Create capability registry for resource setup
        from overblick.core.capability import CapabilityRegistry
        self._services.capability_registry = CapabilityRegistry.default()

        # 8c. Create policy gate
        from overblick.core.security.policy_gate import PolicyGate
        self._services.policy_gate = PolicyGate(
            identity_name=self._identity_name,
            permission_checker=self._services.permissions,
            capability_checker=self._services.capability_checker,
            llm_pipeline=self._services.llm_pipeline,
            preflight_checker=self._services.preflight,
            output_safety=self._services.output_safety,
            rate_limiter=self._services.rate_limiter,
        )

        # 9. Run resource setup pipeline
        resource_setup = ResourceSetup(
            services=self._services,
            runtime_state=self._runtime_state,
            paths=paths,
            identity_name=self._identity_name,
        )
        await resource_setup.setup()

        logger.info("Manual bootstrap complete")

        return OrchestratorBootstrapResult(
            services=self._services,
            runtime_state=self._runtime_state,
            paths=paths,
        )

    # --- Helper methods ---

    async def _create_llm_client(self) -> Any:
        """Create LLM client — routes through GatewayClient."""
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

    def _create_preflight(self) -> Any:
        """Create preflight checker from identity security config."""
        from overblick.core.security.preflight import PreflightChecker

        assert self._services.identity is not None, "Identity must be loaded before creating preflight checker"
        assert self._services.llm_client is not None, "LLM client must be initialized before creating preflight checker"

        if not self._services.identity.security.enable_preflight:
            logger.info("Preflight checker disabled by identity config")
            return None

        admin_ids = set(self._services.identity.security.admin_user_ids)
        deflections = (
            self._services.identity.deflections
            if isinstance(self._services.identity.deflections, dict)
            else {}
        )

        return PreflightChecker(
            llm_client=self._services.llm_client,
            admin_user_ids=admin_ids,
            deflections=deflections,
        )

    def _create_output_safety(self) -> Any:
        """Create output safety filter from identity config."""
        from overblick.core.security.output_safety import OutputSafety

        assert self._services.identity is not None, "Identity must be loaded before creating output safety"

        if not self._services.identity.security.enable_output_safety:
            logger.info("Output safety disabled by identity config")
            return None

        personality = self._services.identity.personality
        banned_slang: list[str] = []
        slang_replacements: dict[str, str] = {}
        if personality:
            vocab = personality.get("vocabulary", {})
            banned_slang = [rf"\b{w}\b" for w in vocab.get("banned_words", [])]
            slang_replacements = vocab.get("slang_replacements", {})

        deflections = self._services.identity.deflections
        deflection_list = deflections if isinstance(deflections, list) else []

        return OutputSafety(
            identity_name=self._identity_name,
            banned_slang_patterns=banned_slang,
            slang_replacements=slang_replacements,
            deflections=deflection_list if deflection_list else None,
        )
