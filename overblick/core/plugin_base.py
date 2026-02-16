"""
Abstract plugin interface + PluginContext.

Plugins are self-contained modules that receive PluginContext as their
ONLY interface to the framework. This ensures clean isolation.

Type annotations use TYPE_CHECKING to avoid circular imports while
providing full IDE/mypy support for all framework services.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

logger = logging.getLogger(__name__)


class PluginContext(BaseModel):
    """
    The ONLY interface plugins have to the Överblick framework.

    Provides controlled access to:
    - Identity configuration
    - Secrets (via get_secret)
    - Data directory (isolated per identity)
    - LLM client (raw) and LLM pipeline (safe)
    - Event bus
    - Scheduler
    - Audit log
    - Engagement DB
    - Security subsystems
    - Permission checker
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    identity_name: str
    data_dir: Path
    log_dir: Path

    # Framework services (set by orchestrator before plugin.setup())
    # Type: overblick.core.llm.client.LLMClient
    llm_client: Any = None
    # Type: overblick.core.event_bus.EventBus
    event_bus: Any = None
    scheduler: Any = None
    # Type: overblick.core.security.audit_log.AuditLog
    audit_log: Any = None
    # Type: overblick.core.quiet_hours.QuietHoursChecker
    quiet_hours_checker: Any = None
    # Type: overblick.core.llm.response_router.ResponseRouter
    response_router: Any = None

    # Type: overblick.core.llm.pipeline.SafeLLMPipeline
    # Preferred over raw llm_client for plugin use
    llm_pipeline: Any = None

    # Type: overblick.identities.Personality
    identity: Any = None

    # Type: overblick.core.db.engagement_db.EngagementDB
    engagement_db: Any = None

    # Type: overblick.core.security.preflight.PreflightChecker
    preflight_checker: Any = None
    # Type: overblick.core.security.output_safety.OutputSafety
    output_safety: Any = None

    # Type: overblick.core.permissions.PermissionChecker
    permissions: Any = None

    # Type: overblick.supervisor.ipc.IPCClient
    ipc_client: Any = None

    # Shared capabilities (populated by orchestrator)
    capabilities: dict[str, Any] = {}

    # Secrets accessor — Callable[[str], Optional[str]]
    _secrets_getter: Any = PrivateAttr(default=None)

    def get_secret(self, key: str) -> Optional[str]:
        """
        Get a secret value by key.

        Args:
            key: Secret key (e.g. "api_key", "agent_id")

        Returns:
            Secret value or None if not found
        """
        if self._secrets_getter:
            return self._secrets_getter(key)
        return None

    def get_capability(self, name: str) -> Optional[Any]:
        """
        Get a capability by name from the shared capabilities dict.

        Args:
            name: Capability name (e.g. "email")

        Returns:
            Capability instance or None if not registered
        """
        return self.capabilities.get(name)

    def load_identity(self, name: str) -> Any:
        """
        Load an identity by name via the framework identity system.

        Plugins should use this instead of importing load_identity directly,
        to maintain plugin isolation from framework internals.

        Args:
            name: Identity name (e.g. "anomal", "cherry")

        Returns:
            Loaded Identity object
        """
        from overblick.identities import load_identity
        return load_identity(name)

    def build_system_prompt(
        self, identity: Any, platform: str = "Moltbook", model_slug: str = "",
    ) -> str:
        """
        Build a system prompt from an identity object.

        Plugins should use this instead of importing build_system_prompt directly,
        to maintain plugin isolation from framework internals.

        Args:
            identity: Loaded Identity object
            platform: Platform name for context (e.g. "Telegram", "Gmail")
            model_slug: LLM model slug for model-specific hints

        Returns:
            System prompt string
        """
        from overblick.identities import build_system_prompt
        return build_system_prompt(identity, platform=platform, model_slug=model_slug)

    def model_post_init(self, __context) -> None:
        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


class PluginBase(ABC):
    """
    Abstract base class for all Överblick plugins.

    Lifecycle:
        1. __init__(ctx) — Receive context, store reference
        2. setup() — Initialize components (async)
        3. tick() — Called periodically by scheduler
        4. teardown() — Cleanup (async)
    """

    def __init__(self, ctx: PluginContext):
        self.ctx = ctx
        self._name = self.__class__.__name__

    @property
    def name(self) -> str:
        """Plugin name (class name by default)."""
        return self._name

    @abstractmethod
    async def setup(self) -> None:
        """
        Initialize plugin components.

        Called once after context is fully populated.
        Raise RuntimeError to prevent plugin from starting.
        """

    @abstractmethod
    async def tick(self) -> None:
        """
        Main plugin work cycle.

        Called periodically by the scheduler. Should be relatively
        quick — long-running work should be spawned as tasks.
        """

    async def teardown(self) -> None:
        """
        Cleanup resources.

        Called on graceful shutdown. Override if cleanup is needed.
        """
        pass

    def __repr__(self) -> str:
        return f"<{self._name} identity={self.ctx.identity_name}>"
