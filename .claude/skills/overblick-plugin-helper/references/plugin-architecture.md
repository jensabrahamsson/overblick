# Plugin Architecture — Full Reference

## PluginBase

**File:** `overblick/core/plugin_base.py`

Abstract base class for all Överblick plugins.

```python
class PluginBase(ABC):
    """
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
        """Plugin name (class name by default, override with class attr)."""

    @abstractmethod
    async def setup(self) -> None:
        """Initialize plugin. Raise RuntimeError to prevent starting."""

    @abstractmethod
    async def tick(self) -> None:
        """Main work cycle. Called periodically by scheduler."""

    async def teardown(self) -> None:
        """Cleanup resources. Default is no-op."""
        pass
```

**Pattern:** Most plugins override `name` as a class attribute:
```python
class TelegramPlugin(PluginBase):
    name = "telegram"  # Class attribute overrides property
```

## PluginContext

**File:** `overblick/core/plugin_base.py`

The ONLY interface plugins have to the framework. Pydantic BaseModel with `arbitrary_types_allowed=True`.

### Fields

```python
class PluginContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Required
    identity_name: str          # e.g. "anomal", "cherry"
    data_dir: Path              # Isolated data dir (auto-created)
    log_dir: Path               # Log dir (auto-created)

    # Framework services (set by orchestrator)
    llm_client: Any = None      # Raw LLM client (Ollama/Gateway)
    event_bus: Any = None        # EventBus for pub/sub
    scheduler: Any = None        # Task scheduler
    audit_log: Any = None        # AuditLog for recording actions
    quiet_hours_checker: Any = None  # QuietHoursChecker
    response_router: Any = None  # Routes responses between plugins

    # Safe LLM pipeline — PREFERRED over raw llm_client
    llm_pipeline: Any = None     # SafeLLMPipeline

    # Identity config (read-only)
    identity: Any = None         # Full Identity object

    # Per-identity engagement database
    engagement_db: Any = None

    # Security subsystems
    preflight_checker: Any = None  # Preflight injection detection
    output_safety: Any = None      # Output filtering

    # Permission checker
    permissions: Any = None        # PermissionChecker

    # Shared capabilities (populated by orchestrator)
    capabilities: dict[str, Any] = {}

    # Secrets accessor (private)
    _secrets_getter: Any = PrivateAttr(default=None)
```

### Methods

```python
def get_secret(self, key: str) -> Optional[str]:
    """Get decrypted secret by key. Returns None if not found."""

def model_post_init(self, __context) -> None:
    """Auto-creates data_dir and log_dir on initialization."""
```

### Data Isolation

The orchestrator creates per-plugin data directories:
```
data/<identity_name>/<plugin_name>/
```

Example for Moltbook running as Anomal:
```
data/anomal/moltbook/   # Plugin data
logs/anomal/            # Shared log directory
```

## PluginRegistry

**File:** `overblick/core/plugin_registry.py`

Security-first plugin discovery. Only loads from `_KNOWN_PLUGINS` whitelist.

### _KNOWN_PLUGINS Whitelist

```python
_KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "gmail": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "discord": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "rss": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "webhook": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
    # Connector aliases (backward-compatible naming)
    "moltbook_connector": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    # ... etc
}
```

### Registry API

```python
class PluginRegistry:
    def register(self, name: str, module_path: str, class_name: str) -> None:
        """Add a plugin to the whitelist (for testing/extensions)."""

    def load(self, name: str, ctx: PluginContext) -> PluginBase:
        """Load and instantiate a plugin. Raises ValueError if unknown."""

    def get(self, name: str) -> Optional[PluginBase]:
        """Get a loaded plugin by name."""

    def all_loaded(self) -> dict[str, PluginBase]:
        """Get all loaded plugins."""

    @staticmethod
    def available_plugins() -> list[str]:
        """List all known plugin names."""
```

### Load Flow

1. Check name exists in `_KNOWN_PLUGINS` (security gate)
2. `importlib.import_module(module_path)` — dynamic but controlled
3. `getattr(module, class_name)` — get the class
4. Verify `issubclass(cls, PluginBase)` — type safety
5. Instantiate with `cls(ctx)` — create plugin
6. Store in `_loaded` dict

## Orchestrator Wiring

**File:** `overblick/core/orchestrator.py`

The orchestrator creates `PluginContext` for each plugin:

```python
# For each plugin:
ctx = PluginContext(
    identity_name=self._identity_name,
    data_dir=data_dir / plugin_name,   # Per-plugin isolation
    log_dir=log_dir,
    llm_client=self._llm_client,
    event_bus=self._event_bus,
    scheduler=self._scheduler,
    audit_log=self._audit_log,
    quiet_hours_checker=self._quiet_hours,
    llm_pipeline=self._llm_pipeline,
    identity=self._identity,
    engagement_db=self._engagement_db,
    preflight_checker=self._preflight,
    output_safety=self._output_safety,
    permissions=self._permissions,
    capabilities=shared_capabilities,
)
```

## SafeLLMPipeline

**File:** `overblick/core/llm/pipeline.py`

6-stage fail-closed pipeline. **All plugins MUST use this instead of raw `llm_client`.**

### Usage

```python
result = await self.ctx.llm_pipeline.chat(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": wrap_external_content(user_input, "source")},
    ],
    user_id="user123",
    audit_action="telegram_response",
    audit_details={"chat_id": 12345},
)

if result.blocked:
    # Handle blocked response
    print(result.block_reason)
    print(result.block_stage)  # Which stage blocked it
    print(result.deflection)   # Safe deflection message (if available)
else:
    response = result.content
```

### PipelineResult

```python
class PipelineResult(BaseModel):
    content: str = ""           # Generated content
    blocked: bool = False       # Whether request was blocked
    block_reason: str = ""      # Why it was blocked
    block_stage: Optional[PipelineStage] = None  # Which stage
    deflection: str = ""        # Safe deflection message
```

### Pipeline Stages

1. **INPUT_SANITIZE** — Strip null bytes, control chars, normalize unicode
2. **PREFLIGHT** — Detect jailbreak/injection attempts
3. **RATE_LIMIT** — Token bucket throttling per identity
4. **LLM_CALL** — Invoke the language model
5. **OUTPUT_SAFETY** — Filter AI leakage, persona breaks, blocked content
6. **AUDIT** — Record the interaction

**Fail-closed:** If any security stage crashes, the request is BLOCKED (not passed through).

## EventBus

**File:** `overblick/core/event_bus.py`

Simple pub/sub for inter-plugin communication.

```python
class EventBus:
    def subscribe(self, event: str, callback: Callable) -> None:
        """Subscribe to an event."""

    async def emit(self, event: str, **kwargs) -> None:
        """Emit an event to all subscribers."""

    async def _safe_call(self, callback, event, **kwargs) -> None:
        """Call subscriber with error isolation."""
```

### Common Events

- `post_created` — New post created
- `comment_posted` — Comment posted
- `message_received` — Message received from external source
- `heartbeat_completed` — Heartbeat cycle completed

## Scheduler

**File:** `overblick/core/scheduler.py`

Task scheduling for periodic work.

```python
class Scheduler:
    def add(self, name: str, callback: Callable, interval: float) -> None:
        """Add a periodic task."""

    def remove(self, name: str) -> None:
        """Remove a scheduled task."""

    def run_immediately(self, name: str) -> None:
        """Run a scheduled task right now."""

    async def start(self) -> None:
        """Start the scheduler loop."""

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
```

## Permission System

**File:** `overblick/core/security/permissions.py`

Default-deny permission system.

```python
class PermissionChecker:
    def is_allowed(self, action: str, context: dict = None) -> bool:
        """Check if an action is permitted."""

    def record_action(self, action: str, details: dict = None) -> None:
        """Record an action for audit trail."""

    @classmethod
    def from_identity(cls, identity) -> "PermissionChecker":
        """Create checker from identity config."""
```

## AuditLog

**File:** `overblick/core/security/audit_log.py`

Append-only SQLite audit log per identity.

```python
class AuditLog:
    def __init__(self, db_path: Path, identity: str):
        """Create audit log backed by SQLite with WAL mode."""

    def log(
        self,
        action: str,              # e.g. "api_call", "llm_request"
        category: str = "general", # e.g. "moltbook", "security"
        plugin: str = None,
        details: dict = None,      # JSON-serializable
        success: bool = True,
        duration_ms: float = None,
        error: str = None,
    ) -> int:
        """Log an action. Returns row ID."""

    def query(
        self,
        action: str = None,
        category: str = None,
        since: float = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query log entries."""

    def count(self, action: str = None, since: float = None) -> int:
        """Count matching entries."""
```

## SecretsManager

**File:** `overblick/core/secrets.py`

Fernet-encrypted secrets with keyring master key.

```python
class SecretsManager:
    def get(self, identity: str, key: str) -> Optional[str]:
        """Get decrypted secret."""

    def set(self, identity: str, key: str, value: str) -> None:
        """Encrypt and store a secret."""

    def has(self, identity: str, key: str) -> bool:
        """Check if secret exists."""

    def list_keys(self, identity: str) -> list[str]:
        """List all secret keys for an identity."""
```

The orchestrator wraps this as `ctx._secrets_getter` so plugins call `ctx.get_secret(key)`.

## Input Sanitizer

**File:** `overblick/core/security/input_sanitizer.py`

### `sanitize(text, max_length=10_000)`
Cleans external input: null byte removal, control char stripping, NFC normalization, length truncation.

### `wrap_external_content(content, source="external")`
Wraps untrusted content with boundary markers to prevent prompt injection:

```python
safe = wrap_external_content(user_message, "telegram_message")
# Result:
# <<<EXTERNAL_TELEGRAM_MESSAGE_START>>>
# <sanitized content>
# <<<EXTERNAL_TELEGRAM_MESSAGE_END>>>
```

**Security:** Iteratively strips boundary marker fragments to prevent nesting attacks.
