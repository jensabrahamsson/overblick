"""
Capability base class — reusable behavioral building blocks for plugins.

Capabilities are composable units of agent behavior (psychology, learning,
social engagement, etc.) that plugins wire together. Each capability has
its own lifecycle (setup/tick/teardown) and can react to events.

This enables Composition over Inheritance: plugins don't need to subclass
a monolithic base — they compose capabilities like lego blocks.

Usage:
    class PsychologyCapability(CapabilityBase):
        name = "psychology"

        async def setup(self) -> None:
            self.dream_log = []

        async def tick(self) -> None:
            if self.ctx.quiet_hours_checker.is_quiet_hours():
                await self._dream()

        async def on_event(self, event: str, **kwargs) -> None:
            if event == "post_created":
                self._reflect(kwargs["content"])
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

if TYPE_CHECKING:
    from overblick.core.plugin_base import PluginContext

logger = logging.getLogger(__name__)


class CapabilityContext(BaseModel):
    """
    Lightweight context passed to capabilities.

    A subset of PluginContext — capabilities get only what they need.
    This prevents capabilities from depending on plugin-specific state.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    identity_name: str
    data_dir: Any  # Path
    llm_client: Any = None
    event_bus: Any = None
    audit_log: Any = None
    quiet_hours_checker: Any = None
    identity: Any = None

    # IPC client for supervisor communication (optional)
    ipc_client: Any = None

    # Safe LLM pipeline (set by plugin or orchestrator)
    llm_pipeline: Any = None

    # Capability-specific config from identity YAML
    config: dict[str, Any] = {}

    # Secrets getter (callable that returns secret value by key)
    _secrets_getter: Any = PrivateAttr(default=None)

    def get_secret(self, key: str) -> str:
        """
        Get a secret value by key.

        Raises:
            KeyError: If secret not found or secrets_getter not configured
        """
        if not self._secrets_getter:
            raise KeyError(f"Secrets not available in capability context (key={key})")
        return self._secrets_getter(key)

    @classmethod
    def from_plugin_context(
        cls,
        ctx: "PluginContext",
        config: Optional[dict[str, Any]] = None,
    ) -> "CapabilityContext":
        """Create a CapabilityContext from a PluginContext."""
        cap_ctx = cls(
            identity_name=ctx.identity_name,
            data_dir=ctx.data_dir,
            llm_client=ctx.llm_client,
            event_bus=ctx.event_bus,
            audit_log=ctx.audit_log,
            quiet_hours_checker=ctx.quiet_hours_checker,
            identity=ctx.identity,
            ipc_client=getattr(ctx, "ipc_client", None),
            llm_pipeline=getattr(ctx, "llm_pipeline", None),
            config=config or {},
        )
        # Set private attributes after creation
        cap_ctx._secrets_getter = getattr(ctx, "_secrets_getter", None)
        return cap_ctx


class CapabilityBase(ABC):
    """
    Abstract base class for reusable agent capabilities.

    Capabilities are behavioral building blocks that plugins compose.
    Each capability has its own lifecycle and can react to events
    from the event bus.

    Lifecycle:
        1. __init__(ctx) — Receive context
        2. setup() — Initialize (async)
        3. tick() — Periodic work (called by plugin, not scheduler directly)
        4. on_event(event, **kwargs) — React to events
        5. teardown() — Cleanup (async)
    """

    # Override in subclass to set a descriptive name
    name: str = "unnamed"

    def __init__(self, ctx: CapabilityContext):
        self.ctx = ctx
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """Whether this capability is currently active."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @abstractmethod
    async def setup(self) -> None:
        """
        Initialize capability state.

        Called once when the owning plugin sets up. Raise RuntimeError
        to signal that this capability cannot start (plugin may choose
        to continue without it).
        """

    async def tick(self) -> None:
        """
        Periodic work cycle.

        Called by the owning plugin during its tick(). Default is no-op —
        not all capabilities need periodic work.
        """

    async def on_event(self, event: str, **kwargs: Any) -> None:
        """
        React to an event from the event bus.

        Override to handle specific events. Default is no-op.

        Args:
            event: Event name (e.g. "post_created", "comment_received")
            **kwargs: Event-specific data
        """

    async def teardown(self) -> None:
        """
        Cleanup resources.

        Called when the owning plugin tears down.
        """

    def get_prompt_context(self) -> str:
        """
        Return context to inject into LLM prompts.

        Override to provide capability-specific context (e.g. dream
        insights, mood hints, knowledge snippets). Default returns "".
        """
        return ""

    def __repr__(self) -> str:
        state = "enabled" if self._enabled else "disabled"
        return f"<{self.__class__.__name__}({self.name}) {state}>"


class CapabilityRegistry:
    """
    Registry for discovering and instantiating capabilities.

    Loads capabilities by name (e.g. "dream_system") or by bundle
    (e.g. "psychology" = dream + therapy + emotional). Each capability
    is instantiated with its own CapabilityContext.
    """

    def __init__(self):
        self._registry: dict[str, type[CapabilityBase]] = {}
        self._bundles: dict[str, list[str]] = {}

    def register(self, name: str, cls: type[CapabilityBase]) -> None:
        """Register a capability class by name."""
        self._registry[name] = cls

    def register_bundle(self, name: str, capability_names: list[str]) -> None:
        """Register a named bundle of capabilities."""
        self._bundles[name] = capability_names

    def resolve(self, names: list[str]) -> list[str]:
        """Resolve names (expanding bundles) to individual capability names."""
        resolved = []
        for name in names:
            if name in self._bundles:
                for cap_name in self._bundles[name]:
                    if cap_name not in resolved:
                        resolved.append(cap_name)
            elif name in self._registry:
                if name not in resolved:
                    resolved.append(name)
            else:
                logger.warning("Unknown capability or bundle: %s", name)
        return resolved

    def create(
        self,
        name: str,
        ctx: "PluginContext",
        config: Optional[dict[str, Any]] = None,
    ) -> Optional[CapabilityBase]:
        """Create a single capability instance from a PluginContext."""
        cls = self._registry.get(name)
        if not cls:
            logger.warning("Capability not found in registry: %s", name)
            return None

        cap_ctx = CapabilityContext.from_plugin_context(ctx, config=config)
        return cls(cap_ctx)

    def create_all(
        self,
        names: list[str],
        ctx: "PluginContext",
        configs: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[CapabilityBase]:
        """Create multiple capabilities, resolving bundles."""
        configs = configs or {}
        resolved = self.resolve(names)
        capabilities = []
        for name in resolved:
            cap = self.create(name, ctx, config=configs.get(name, {}))
            if cap:
                capabilities.append(cap)
        return capabilities

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        """Create a registry pre-loaded with all built-in capabilities."""
        from overblick.capabilities import CAPABILITY_REGISTRY, CAPABILITY_BUNDLES

        registry = cls()
        for name, cap_cls in CAPABILITY_REGISTRY.items():
            registry.register(name, cap_cls)
        for name, cap_names in CAPABILITY_BUNDLES.items():
            registry.register_bundle(name, cap_names)
        return registry
