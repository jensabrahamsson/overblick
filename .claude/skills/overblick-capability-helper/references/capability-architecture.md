# Capability Architecture — Full Reference

## CapabilityBase

**File:** `overblick/core/capability.py`

Abstract base class for reusable agent capabilities.

```python
class CapabilityBase(ABC):
    """
    Capabilities are behavioral building blocks that plugins compose.
    Each capability has its own lifecycle and can react to events.

    Lifecycle:
        1. __init__(ctx) — Receive context
        2. setup() — Initialize (async)
        3. tick() — Periodic work (called by plugin, not scheduler directly)
        4. on_event(event, **kwargs) — React to events
        5. teardown() — Cleanup (async)
    """

    # Override in subclass
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
        """Initialize. Raise RuntimeError to signal failure."""

    async def tick(self) -> None:
        """Periodic work. Default is no-op."""

    async def on_event(self, event: str, **kwargs: Any) -> None:
        """React to event. Default is no-op."""

    async def teardown(self) -> None:
        """Cleanup. Default is no-op."""

    def get_prompt_context(self) -> str:
        """Return context for LLM prompts. Default returns ''."""
        return ""

    def __repr__(self) -> str:
        state = "enabled" if self._enabled else "disabled"
        return f"<{self.__class__.__name__}({self.name}) {state}>"
```

### Key Design Points

- **Only `setup()` is abstract** — all other lifecycle methods have sensible defaults
- **Not all capabilities need `tick()`** — only override if periodic work is needed
- **`get_prompt_context()`** allows capabilities to inject context into LLM prompts without the plugin knowing the details
- **`enabled` property** allows runtime enable/disable (e.g., during quiet hours)

## CapabilityContext

**File:** `overblick/core/capability.py`

A lightweight subset of `PluginContext` — capabilities get only what they need.

```python
class CapabilityContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    identity_name: str          # e.g. "anomal"
    data_dir: Any               # Path — shared with plugin
    llm_client: Any = None      # Raw LLM client (avoid in new code)
    event_bus: Any = None        # EventBus for pub/sub
    audit_log: Any = None        # AuditLog
    quiet_hours_checker: Any = None
    identity: Any = None         # Full Identity object
    llm_pipeline: Any = None     # SafeLLMPipeline (preferred for LLM calls)

    # Capability-specific config from identity YAML
    config: dict[str, Any] = {}

    # Secrets getter (private — callable returning secret value by key)
    _secrets_getter: Any = PrivateAttr(default=None)
```

### Methods

```python
def get_secret(self, key: str) -> str:
    """Get a secret value by key.

    Raises:
        KeyError: If secret not found or secrets_getter not configured
    """

@classmethod
def from_plugin_context(
    cls,
    ctx: "PluginContext",
    config: Optional[dict[str, Any]] = None,
) -> "CapabilityContext":
    """Create a CapabilityContext from a PluginContext.

    Copies: identity_name, data_dir, llm_client, event_bus, audit_log,
    quiet_hours_checker, identity, llm_pipeline, _secrets_getter.
    """
```

### `from_plugin_context()` — Factory Method

```python
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
        llm_pipeline=getattr(ctx, "llm_pipeline", None),
        config=config or {},
    )
    # Set private attributes after creation
    cap_ctx._secrets_getter = getattr(ctx, "_secrets_getter", None)
    return cap_ctx
```

**Pattern:** Plugins create capability contexts like this:
```python
cap_ctx = CapabilityContext.from_plugin_context(self.ctx, config={"dream_frequency": 2})
cap = DreamCapability(cap_ctx)
await cap.setup()
```

Or via the registry (which does this automatically):
```python
registry = CapabilityRegistry.default()
cap = registry.create("dream_system", self.ctx, config={"dream_frequency": 2})
```

## CapabilityRegistry

**File:** `overblick/core/capability.py`

Registry for discovering and instantiating capabilities by name or bundle.

```python
class CapabilityRegistry:
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
        """Resolve names (expanding bundles) to individual capability names.

        Example:
            resolve(["knowledge", "summarizer"])
            → ["safe_learning", "knowledge_loader", "summarizer"]
        """

    def create(
        self,
        name: str,
        ctx: "PluginContext",
        config: Optional[dict] = None,
    ) -> Optional[CapabilityBase]:
        """Create a single capability instance from a PluginContext.

        Internally creates CapabilityContext via from_plugin_context().
        Returns None if capability not found in registry.
        """

    def create_all(
        self,
        names: list[str],
        ctx: "PluginContext",
        configs: Optional[dict[str, dict]] = None,
    ) -> list[CapabilityBase]:
        """Create multiple capabilities, resolving bundles.

        configs maps capability name -> config dict.
        """

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        """Create registry pre-loaded with all built-in capabilities.

        Loads from CAPABILITY_REGISTRY and CAPABILITY_BUNDLES in
        overblick/capabilities/__init__.py.
        """
```

### Registration Tables

**`CAPABILITY_REGISTRY`** — name-to-class mapping (21 capabilities):
```python
CAPABILITY_REGISTRY = {
    # psychology (DEPRECATED — use personality.yaml instead)
    "dream_system": DreamCapability,
    "therapy_system": TherapyCapability,
    "emotional_state": EmotionalCapability,
    "mood_cycle": MoodCycleCapability,
    # knowledge
    "safe_learning": LearningCapability,
    "knowledge_loader": KnowledgeCapability,
    # social
    "openings": OpeningCapability,
    # engagement
    "analyzer": AnalyzerCapability,
    "composer": ComposerCapability,
    # conversation
    "conversation_tracker": ConversationCapability,
    # content
    "summarizer": SummarizerCapability,
    # speech
    "stt": SpeechToTextCapability,
    "tts": TextToSpeechCapability,
    # vision
    "vision": VisionCapability,
    # communication
    "boss_request": BossRequestCapability,
    "email": EmailCapability,
    "gmail": GmailCapability,
    "style_trainer": StyleTrainerCapability,
    "telegram_notifier": TelegramNotifier,
    # monitoring
    "host_inspection": HostInspectionCapability,
    # system
    "system_clock": SystemClockCapability,
    # consulting
    "personality_consultant": PersonalityConsultantCapability,
}
```

**`CAPABILITY_BUNDLES`** — bundle-to-names mapping (12 bundles):
```python
CAPABILITY_BUNDLES = {
    "system": ["system_clock"],
    "psychology": ["dream_system", "therapy_system", "emotional_state", "mood_cycle"],  # DEPRECATED
    "knowledge": ["safe_learning", "knowledge_loader"],
    "social": ["openings"],
    "engagement": ["analyzer", "composer"],
    "conversation": ["conversation_tracker"],
    "content": ["summarizer"],
    "speech": ["stt", "tts"],
    "vision": ["vision"],
    "communication": ["boss_request", "email", "gmail", "style_trainer", "telegram_notifier"],
    "consulting": ["personality_consultant"],
    "monitoring": ["host_inspection"],
}
```

### Bundle Resolution Flow

1. Plugin calls `registry.resolve(["knowledge", "summarizer"])`
2. `"knowledge"` is a bundle → expands to `["safe_learning", "knowledge_loader"]`
3. `"summarizer"` is a capability → kept as-is
4. Returns: `["safe_learning", "knowledge_loader", "summarizer"]`
5. Unknown names are logged as warnings and skipped

### How Plugins Consume Capabilities

**Method 1: Via shared capabilities from orchestrator**
```python
shared_caps = getattr(self.ctx, "capabilities", {}) or {}
tracker = shared_caps.get("conversation_tracker")
if tracker:
    tracker.add_user_message(str(chat_id), text)
```

**Method 2: Via PluginContext helper method**
```python
tracker = self.ctx.get_capability("conversation_tracker")
if tracker:
    tracker.add_user_message(str(chat_id), text)
```

**Method 3: Via registry (create locally)**
```python
registry = CapabilityRegistry.default()
resolved = registry.resolve(enabled_modules)
for name in resolved:
    cap = registry.create(name, self.ctx, config=configs.get(name, {}))
    if cap:
        await cap.setup()
        self._capabilities[cap.name] = cap
```

**Method 4: Direct instantiation (for tests/simple cases)**
```python
from overblick.core.capability import CapabilityContext
from overblick.capabilities.content.summarizer import SummarizerCapability

cap_ctx = CapabilityContext(identity_name="test", data_dir=Path("/tmp"))
cap = SummarizerCapability(cap_ctx)
await cap.setup()
```
