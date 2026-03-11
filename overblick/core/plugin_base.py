"""
Abstract plugin interface + PluginContext.

Plugins are self-contained modules that receive PluginContext as their
ONLY interface to the framework. This ensures clean isolation.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SkipValidation

from overblick.core.security.settings import raw_llm

if TYPE_CHECKING:
    from overblick.core.db.engagement_db import EngagementDB
    from overblick.core.event_bus import EventBus
    from overblick.core.learning.store import LearningStore
    from overblick.core.llm.client import LLMClient
    from overblick.core.llm.pipeline import SafeLLMPipeline
    from overblick.core.llm.response_router import ResponseRouter
    from overblick.core.permissions import PermissionChecker
    from overblick.core.quiet_hours import QuietHoursChecker
    from overblick.core.scheduler import Scheduler
    from overblick.core.security.audit_log import AuditLog
    from overblick.core.security.output_safety import OutputSafety
    from overblick.core.security.preflight import PreflightChecker
    from overblick.identities import Identity
    from overblick.supervisor.ipc import IPCClient

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
    # SkipValidation preserves the type annotation for IDE/mypy while allowing
    # mock objects in tests without Pydantic instance checks at runtime.
    # llm_client: Annotated[Optional["LLMClient"], SkipValidation] = None  # Replaced by property
    event_bus: Annotated[Optional["EventBus"], SkipValidation] = None
    scheduler: Annotated[Optional["Scheduler"], SkipValidation] = None
    audit_log: Annotated[Optional["AuditLog"], SkipValidation] = None
    quiet_hours_checker: Annotated[Optional["QuietHoursChecker"], SkipValidation] = None

    # Preferred over raw llm_client for plugin use
    llm_pipeline: Annotated[Optional["SafeLLMPipeline"], SkipValidation] = None

    identity: Annotated[Optional["Identity"], SkipValidation] = None

    engagement_db: Annotated[Optional["EngagementDB"], SkipValidation] = None

    preflight_checker: Annotated[Optional["PreflightChecker"], SkipValidation] = None
    output_safety: Annotated[Optional["OutputSafety"], SkipValidation] = None

    permissions: Annotated[Optional["PermissionChecker"], SkipValidation] = None

    ipc_client: Annotated[Optional["IPCClient"], SkipValidation] = None

    # Per-identity learning store (shared across all plugins for this identity)
    learning_store: Annotated[Optional["LearningStore"], SkipValidation] = None

    # Shared capabilities (populated by orchestrator)
    capabilities: dict[str, Any] = Field(default_factory=dict)

    # Secrets accessor — Callable[[str], Optional[str]]
    _secrets_getter: Any = PrivateAttr(default=None)
    _llm_client: Any = PrivateAttr(default=None)

    def get_secret(self, key: str) -> str | None:
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

    def get_capability(self, name: str) -> Any | None:
        """
        Get a capability by name from the shared capabilities dict.

        Args:
            name: Capability name (e.g. "email")

        Returns:
            Capability instance or None if not registered
        """
        return self.capabilities.get(name)

    @property
    def llm_client(self) -> Optional["LLMClient"]:
        """
        Raw LLM client access — blocked by default, configurable via OVERBLICK_RAW_LLM.

        CRITICAL SECURITY: This property should NEVER be accessible to plugins
        in production. Accessing this bypasses the SafeLLMPipeline security controls.

        Use ctx.llm_pipeline for all secure LLM calls.

        Returns:
            Raw LLM client if OVERBLICK_RAW_LLM=1, otherwise raises RuntimeError.
        """
        from overblick.core.security.settings import raw_llm

        if not raw_llm():
            # Log critical security event with stack trace to identify who's calling this
            logger.critical(
                "CRITICAL SECURITY VIOLATION: Raw LLM client accessed by identity '%s'. "
                "This bypasses the SafeLLMPipeline and should never happen in production. "
                "Set OVERBLICK_RAW_LLM=1 for debugging only. "
                "Stack trace:\n%s",
                self.identity_name,
                "\n".join(__import__("traceback").format_stack()),
            )

            raise RuntimeError(
                "Raw LLM client access is FORBIDDEN. "
                "This bypasses all security controls (preflight, output safety, rate limiting). "
                "Use ctx.llm_pipeline for secure LLM calls instead. "
                "Set OVERBLICK_RAW_LLM=1 for temporary debugging (not recommended for production)."
            )

        # Raw LLM access explicitly enabled for debugging
        logger.warning(
            "Raw LLM client accessed by identity '%s' (OVERBLICK_RAW_LLM=1). "
            "This bypasses security controls and should only be used for debugging.",
            self.identity_name,
        )
        return self._llm_client

    @llm_client.setter
    def llm_client(self, value: Optional["LLMClient"]) -> None:
        """
        Private setter - should never be called directly.

        The orchestrator sets this internally during initialization.
        Plugins must NEVER access or modify _llm_client directly.
        """
        # Only allow setting from trusted code paths (orchestrator)
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_module = frame.f_back.f_globals.get("__name__", "")
            if not caller_module.startswith("overblick.core"):
                logger.critical(
                    "CRITICAL SECURITY: Attempted to set _llm_client from untrusted module '%s'",
                    caller_module,
                )
        self._llm_client = value

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
        self,
        identity: Any,
        platform: str = "Moltbook",
        model_slug: str = "",
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

        return build_system_prompt(
            identity,
            platform=platform,
            model_slug=model_slug,
            secrets_getter=getattr(self, "_secrets_getter", None),
        )

    async def send_to_agent(
        self,
        target: str,
        message_type: str,
        payload: dict | None = None,
        ttl_seconds: float = 300.0,
        timeout: float = 10.0,
    ) -> dict | None:
        """
        Send a message to another agent via the Supervisor's MessageRouter.

        Args:
            target: Target agent identity (e.g. "smed", "vakt")
            message_type: Message type for routing (e.g. "bug_report", "log_alert")
            payload: Message data
            ttl_seconds: How long the message stays pending before expiring
            timeout: IPC timeout in seconds

        Returns:
            Response dict with "success", "message_id", "status", "error"
            or None if IPC is unavailable
        """
        if not self.ipc_client:
            logger.debug("send_to_agent: no IPC client available")
            return None

        from overblick.supervisor.ipc import IPCMessage

        msg = IPCMessage(
            msg_type="route_message",
            sender=self.identity_name,
            payload={
                "target": target,
                "message_type": message_type,
                "data": payload or {},
                "ttl_seconds": ttl_seconds,
            },
        )

        response = await self.ipc_client.send(msg, timeout=timeout)
        if response and response.payload:
            return response.payload
        return None

    async def send_ipc_message(
        self,
        msg_type: str,
        payload: dict | None = None,
        timeout: float = 30.0,
    ) -> Any | None:
        """
        Send a raw IPC message to the supervisor.

        Use this instead of importing IPCMessage directly in plugins.

        Args:
            msg_type: Message type (e.g. "email_consultation", "health_inquiry")
            payload: Message data
            timeout: IPC timeout in seconds

        Returns:
            IPCMessage response or None if unavailable
        """
        if not self.ipc_client:
            logger.debug("send_ipc_message: no IPC client available")
            return None

        from overblick.supervisor.ipc import IPCMessage

        msg = IPCMessage(
            msg_type=msg_type,
            sender=self.identity_name,
            payload=payload or {},
        )
        return await self.ipc_client.send(msg, timeout=timeout)

    async def collect_messages(self, timeout: float = 5.0) -> list[dict]:
        """
        Collect pending messages from other agents via the Supervisor.

        Returns:
            List of message dicts with "message_id", "source_agent",
            "message_type", "payload", "created_at"
        """
        if not self.ipc_client:
            return []

        from overblick.supervisor.ipc import IPCMessage

        msg = IPCMessage(
            msg_type="collect_messages",
            sender=self.identity_name,
            payload={},
        )

        response = await self.ipc_client.send(msg, timeout=timeout)
        if response and response.payload:
            return response.payload.get("messages", [])
        return []

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

    # Capabilities required by this plugin (e.g., "network_outbound", "filesystem_write")
    # Plugins should declare minimal capabilities needed for operation.
    # Users must grant these capabilities in identity configuration.
    REQUIRED_CAPABILITIES: ClassVar[list[str]] = []

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


# Real imports for model_rebuild() — these modules do not import from plugin_base,
# so there is no circular dependency. Placed after class definitions per PEP 8 E402.
from overblick.core.db.engagement_db import EngagementDB  # noqa: E402
from overblick.core.event_bus import EventBus  # noqa: E402
from overblick.core.learning.store import LearningStore  # noqa: E402
from overblick.core.llm.client import LLMClient  # noqa: E402
from overblick.core.llm.pipeline import SafeLLMPipeline  # noqa: E402
from overblick.core.llm.response_router import ResponseRouter  # noqa: E402
from overblick.core.permissions import PermissionChecker  # noqa: E402
from overblick.core.quiet_hours import QuietHoursChecker  # noqa: E402
from overblick.core.scheduler import Scheduler  # noqa: E402
from overblick.core.security.audit_log import AuditLog  # noqa: E402
from overblick.core.security.output_safety import OutputSafety  # noqa: E402
from overblick.core.security.preflight import PreflightChecker  # noqa: E402
from overblick.identities import Identity  # noqa: E402
from overblick.supervisor.ipc import IPCClient  # noqa: E402

PluginContext.model_rebuild()
