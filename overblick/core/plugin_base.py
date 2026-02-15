"""
Abstract plugin interface + PluginContext.

Plugins are self-contained modules that receive PluginContext as their
ONLY interface to the framework. This ensures clean isolation.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

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
    llm_client: Any = None
    event_bus: Any = None
    scheduler: Any = None
    audit_log: Any = None
    quiet_hours_checker: Any = None
    response_router: Any = None

    # Safe LLM pipeline (consolidates all security checks)
    # Preferred over raw llm_client for plugin use
    llm_pipeline: Any = None

    # Identity config (read-only)
    identity: Any = None

    # Per-identity engagement database
    engagement_db: Any = None

    # Security subsystems
    preflight_checker: Any = None
    output_safety: Any = None

    # Permission checker (action authorization)
    permissions: Any = None

    # IPC client (for inter-agent communication via supervisor)
    ipc_client: Any = None

    # Shared capabilities (populated by orchestrator)
    capabilities: dict[str, Any] = {}

    # Secrets accessor (callable)
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


# Connector aliases — new naming convention (backward-compatible)
ConnectorBase = PluginBase
AgentContext = PluginContext
