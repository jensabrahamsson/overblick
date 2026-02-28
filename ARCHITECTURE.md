# Architecture

Överblick is a security-first, multi-identity agent framework. It runs multiple AI personalities as isolated processes, each with its own plugins, capabilities, secrets, and data — unified by a shared security pipeline and managed by a supervisor (boss agent).

This document describes every layer of the system.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Orchestrator](#orchestrator)
3. [Identity System](#identity-system)
4. [Personality System](#personality-system)
5. [Plugin Architecture](#plugin-architecture)
6. [Capability System](#capability-system)
7. [Security Architecture](#security-architecture)
8. [LLM Subsystem](#llm-subsystem)
9. [Supervisor (Boss Agent)](#supervisor-boss-agent)
10. [Database Layer](#database-layer)
11. [Learning System](#learning-system)
12. [Event Bus](#event-bus)
13. [Scheduler](#scheduler)
14. [Permission System](#permission-system)
15. [Quiet Hours](#quiet-hours)
16. [Data Isolation](#data-isolation)
17. [Configuration](#configuration)
18. [Testing](#testing)

---

## System Overview

```
                        ┌─────────────────────┐
                        │     Supervisor       │
                        │    (Boss Agent)      │
                        │                     │
                        │  Process lifecycle   │
                        │  IPC (Unix sockets)  │
                        │  Permission grants   │
                        │  Agent audit         │
                        └──────────┬──────────┘
                                   │ HMAC-authenticated IPC
                 ┌─────────────────┼─────────────────┐
                 │                 │                 │
          ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
          │ Orchestrator │  │ Orchestrator │  │ Orchestrator │
          │  "anomal"    │  │  "cherry"    │  │  "blixt"     │
          └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
                 │                │                 │
          ┌──────▼──────────────────────────────────▼──────┐
          │                 Per-Identity                    │
          │  ┌──────────┐ ┌────────────┐ ┌──────────────┐  │
          │  │ Identity  │ │Personality │ │ Capabilities │  │
          │  │ (YAML)    │ │ (YAML)     │ │ (Lego)       │  │
          │  └──────────┘ └────────────┘ └──────────────┘  │
          │  ┌──────────┐ ┌────────────┐ ┌──────────────┐  │
          │  │ Plugins   │ │ Event Bus  │ │  Scheduler   │  │
          │  │ (Connectors)│ │ (Pub/Sub) │ │ (Periodic)   │  │
          │  └──────┬───┘ └────────────┘ └──────────────┘  │
          └─────────┼──────────────────────────────────────┘
                    │
          ┌─────────▼─────────────────────────────────────┐
          │              Security Layer                    │
          │                                               │
          │  Input         Preflight    Rate       Output  │
          │  Sanitizer  →  Checker   →  Limiter →  Safety  │
          │  (boundary     (3-layer     (token     (AI     │
          │   markers)      anti-       bucket)    leakage │
          │                jailbreak)              filter) │
          │                                               │
          │  ┌──────────────────────────────────────────┐ │
          │  │         SafeLLMPipeline                   │ │
          │  │  sanitize → preflight → rate limit →     │ │
          │  │  LLM call → output safety → audit        │ │
          │  │         (FAIL-CLOSED)                     │ │
          │  └──────────────────────────────────────────┘ │
          │                                               │
          │  SecretsManager  │  AuditLog  │  Permissions  │
          │  (Fernet + keyring) (SQLite)    (default-deny) │
          └───────────────────────────────────────────────┘
                    │
          ┌─────────▼──────────────────────┐
          │       LLM Backend              │
          │  ┌────────┐ ┌────────┐ ┌─────┐ │
          │  │ Ollama  │ │Gateway │ │Cloud│ │
          │  │(local)  │ │(queue) │ │(API)│ │
          │  └────────┘ └────────┘ └─────┘ │
          └────────────────────────────────┘
```

**Key design principles:**

- **Security-first:** Every LLM interaction passes through a fail-closed 6-stage pipeline. If any security stage crashes, the request is blocked (not passed through).
- **Identity isolation:** Each agent identity has its own data directory, log directory, secrets, and audit log. No cross-contamination.
- **Composition over inheritance:** Plugins compose capabilities (lego blocks) rather than inheriting from monolithic base classes.
- **YAML-driven:** All behavioral differences are controlled by YAML configuration — identity settings, personality traits, permissions, and schedules.
- **Plugin isolation:** Plugins access the framework exclusively through `PluginContext`. No global state, no importing core internals.

---

## Orchestrator

**File:** `overblick/core/orchestrator.py`

The Orchestrator is the top-level lifecycle manager for a single agent identity. It wires together all framework components and manages the full lifecycle.

### Lifecycle States

```
INIT → SETUP → RUNNING → STOPPING → STOPPED
```

### Setup Sequence

When `Orchestrator.run()` is called, setup proceeds in this exact order:

```
1. Load Identity          load_identity("anomal")
2. Create Paths           data/<identity>/, logs/<identity>/
3. Initialize Security    SecretsManager, AuditLog
4. Setup Quiet Hours      QuietHoursChecker from identity settings
5. Create LLM Client      OllamaClient (with health check)
6. Create Security Chain   PreflightChecker, OutputSafety, RateLimiter
7. Build Pipeline          SafeLLMPipeline (wraps all security + LLM)
8. Setup Capabilities      Resolve bundles → create → setup each
9. Load Plugins            For each connector: create PluginContext →
                           load plugin from registry → call setup()
```

After setup, the orchestrator registers each plugin's `tick()` in the scheduler, then runs the scheduler and a shutdown event listener concurrently. `SIGINT`/`SIGTERM` triggers graceful shutdown.

### Shutdown

Teardown reverses the setup order:
1. Stop scheduler
2. Teardown plugins (reverse order)
3. Close LLM client
4. Final audit log entry
5. Clear event bus

### Usage

```python
# Single identity
orch = Orchestrator(identity_name="anomal")
await orch.run()  # Blocks until SIGINT/SIGTERM

# CLI
python -m overblick run anomal
```

---

## Identity System

**File:** `overblick/identities/__init__.py`

An Identity is a frozen Pydantic model loaded from YAML that controls all operational behavior for one agent. Identities are NOT personalities — they define *what the agent does*, not *who it is*.

### Identity Fields

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | Internal name (directory name) |
| `display_name` | `str` | Human-readable name |
| `engagement_threshold` | `int` | Score above which agent engages with content |
| `connectors` | `tuple[str, ...]` | Plugins/connectors to load |
| `capability_names` | `tuple[str, ...]` | Capabilities to enable (bundles expand) |
| `llm` | `LLMSettings` | Model, provider, temperature, max_tokens, timeout |
| `quiet_hours` | `QuietHoursSettings` | Start/end hours, timezone |
| `schedule` | `ScheduleSettings` | Heartbeat, feed poll intervals |
| `security` | `SecuritySettings` | Preflight/output safety toggle, admin IDs |
| `identity_ref` | `str` | Name of identity/personality to load |
| `interest_keywords` | `list[str]` | Keywords for engagement scoring |
| `deflections` | `dict/list` | Identity-specific deflection phrases |
| `raw_config` | `dict` | Full YAML for arbitrary plugin access |

### Sub-Settings (Frozen)

Each settings group is its own frozen Pydantic model:

- **`LLMSettings`** — provider ("ollama"|"gateway"|"cloud"), model, temperature, top_p, max_tokens, timeout_seconds, cloud_api_url, cloud_model, cloud_secret_key
- **`QuietHoursSettings`** — enabled, timezone, start_hour, end_hour, mode
- **`ScheduleSettings`** — heartbeat_hours, feed_poll_minutes, enabled
- **`SecuritySettings`** — enable_preflight, enable_output_safety, admin_user_ids, block_threshold

### Loading

```python
identity = load_identity("anomal")
# Reads: overblick/identities/anomal/identity.yaml (required)
# Also loads: personality.yaml, opinions.yaml, opsec.yaml, knowledge_*.yaml
# Auto-loads: personality via identity_ref
```

### Identity-Personality Wiring

The `identity_ref` field (defaults to the identity name) tells the loader which personality to attach:

```yaml
# identity.yaml
name: anomal
identity_ref: anomal  # → loads identities/anomal/personality.yaml
```

After loading:
```python
identity.loaded_personality        # Personality object
identity.loaded_personality.voice  # Voice config dict
```

---

## Personality System

**File:** `overblick/identities/__init__.py`

Identities define *who the agent is* — voice, backstory, traits, interests, vocabulary, and conversational examples. They are reusable: the same identity can be referenced by multiple configurations.

### Identity Class

Frozen Pydantic model containing all character data:

```python
class Identity(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    display_name: str
    version: str
    identity_info: dict[str, Any]     # From "identity:" section
    backstory: dict[str, Any]         # Origin story, goals
    voice: dict[str, Any]             # Tone, style, humor, length
    traits: dict[str, float]          # 0.0-1.0 scale (Big Five + custom)
    interests: dict[str, Any]         # Domain → enthusiasm + topics + perspective
    vocabulary: dict[str, Any]        # Preferred + banned words
    signature_phrases: dict[str, list[str]]  # Greetings, reactions, etc.
    ethos: dict[str, Any] | list[str] # Core principles
    examples: dict[str, Any]          # Few-shot conversation examples
    moltbook_bio: str                 # Social platform bio
    raw: dict[str, Any]               # Complete YAML data
```

### Loading Search Order

`load_identity(name)` searches three locations:

1. `overblick/identities/<name>/personality.yaml` (directory-based, preferred)
2. `overblick/identities/<name>.yaml` (standalone file)
3. `overblick/personalities/<name>/personality.yaml` (legacy location)

### System Prompt Generation

`build_system_prompt(personality, platform="Moltbook")` generates LLM system prompts with these sections in order:

1. **Identity** — "You are {name}, participating on {platform}."
2. **Role & description** — From identity_info
3. **Voice** — base_tone, style, humor_style, default_length
4. **Strong/Low traits** — Only traits >= 0.8 (strong) and <= 0.25 (low)
5. **Ethos** — Core principles (up to 5)
6. **Signature phrases** — Typical openings
7. **Vocabulary** — Banned words (up to 20) + preferred words (up to 15)
8. **Examples** — Up to 2 conversation examples (few-shot)
9. **Security block** — Always appended (anti-injection rules)

### Personality Stable

| Name | Archetype | Key Traits |
|------|-----------|------------|
| **Anomal** | Intellectual humanist | openness 0.92, cerebral 0.88, warmth 0.75 |
| **Cherry** | 28yo Stockholm woman | extraversion 0.85, warmth 0.80, humor 0.75 |
| **Blixt** | Punk tech critic | openness 0.85, agreeableness 0.30, patience 0.25 |
| **Birch** | Forest philosopher | introversion 0.90, patience 0.95, calm 0.95 |
| **Prism** | Digital artist | openness 0.98, creativity 0.98, curiosity 0.95 |
| **Rust** | Jaded ex-trader | neuroticism 0.55, genuineness 0.90, humor 0.75 |
| **Nyx** | Uncanny philosopher | openness 0.90, cerebral 0.95, warmth 0.25 |

---

## Plugin Architecture

**Files:** `overblick/core/plugin_base.py`, `overblick/core/plugin_registry.py`

Plugins (also called connectors) are self-contained modules that implement a specific integration (Telegram bot, email agent, social platform bot). Each plugin receives `PluginContext` as its **only** interface to the framework.

### PluginBase

```python
class PluginBase(ABC):
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx

    async def setup(self) -> None: ...    # Initialize (required)
    async def tick(self) -> None: ...     # Periodic work (required)
    async def teardown(self) -> None: ... # Cleanup (optional)
```

### PluginContext

The sole framework interface. Provides controlled access to everything a plugin needs:

| Field | Type | Purpose |
|-------|------|---------|
| `identity_name` | `str` | Which identity this plugin runs as |
| `data_dir` | `Path` | Isolated data directory |
| `log_dir` | `Path` | Log directory |
| `llm_pipeline` | `SafeLLMPipeline` | **Preferred** — full security chain |
| `llm_client` | `LLMClient` | Raw LLM (use pipeline instead) |
| `event_bus` | `EventBus` | Pub/sub communication |
| `scheduler` | `Scheduler` | Periodic task registration |
| `audit_log` | `AuditLog` | Structured action logging |
| `quiet_hours_checker` | `QuietHoursChecker` | Bedroom mode check |
| `identity` | `Identity` | Read-only identity config |
| `permissions` | `PermissionChecker` | Action authorization |
| `capabilities` | `dict[str, CapabilityBase]` | Shared capabilities |
| `get_secret(key)` | method | Fernet-encrypted secrets |
| `preflight_checker` | `PreflightChecker` | Pre-posting checks |
| `output_safety` | `OutputSafety` | Post-generation filter |

### Plugin Registry

```python
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    "ai_digest":   ("overblick.plugins.ai_digest.plugin",   "AiDigestPlugin"),
    "compass":     ("overblick.plugins.compass.plugin",      "CompassPlugin"),
    "dev_agent":   ("overblick.plugins.dev_agent.plugin",    "DevAgentPlugin"),
    "email_agent": ("overblick.plugins.email_agent.plugin",  "EmailAgentPlugin"),
    "github":      ("overblick.plugins.github.plugin",       "GitHubPlugin"),
    "host_health": ("overblick.plugins.host_health.plugin",  "HostHealthPlugin"),
    "irc":         ("overblick.plugins.irc.plugin",          "IRCPlugin"),
    "kontrast":    ("overblick.plugins.kontrast.plugin",     "KontrastPlugin"),
    "moltbook":    ("overblick.plugins.moltbook.plugin",     "MoltbookPlugin"),
    "skuggspel":   ("overblick.plugins.skuggspel.plugin",    "SkuggspelPlugin"),
    "spegel":      ("overblick.plugins.spegel.plugin",       "SpegelPlugin"),
    "stage":       ("overblick.plugins.stage.plugin",        "StagePlugin"),
    "telegram":    ("overblick.plugins.telegram.plugin",     "TelegramPlugin"),
}
```

The registry uses a **whitelist** — only plugins in `_KNOWN_PLUGINS` can be loaded. `PluginRegistry.load(name, ctx)` does:

1. Validate name is in whitelist
2. Import module dynamically
3. Verify the class exists and is a `PluginBase` subclass
4. Instantiate with `cls(ctx)`

### Available Plugins

| Plugin | Status | Description |
|--------|--------|-------------|
| **Moltbook** | Production | Autonomous social engagement (OBSERVE → THINK → DECIDE → ACT → LEARN) |
| **Telegram** | Complete | Bot with commands, conversation tracking, rate limiting |
| **Email Agent** | Complete | LLM-driven email classification, reply, notification |
| **GitHub Agent** | Complete | Agentic GitHub issue/PR handling with OBSERVE/THINK/PLAN/ACT/REFLECT loop |
| **Dev Agent** | Complete | Autonomous developer — log watching, bug fixing, test running, PR creation |
| **IRC** | Complete | Identity-to-identity conversations with topic management |
| **AI Digest** | Complete | RSS-powered daily news digest with personality voice |
| **Host Health** | Complete | System health monitoring with Supervisor IPC |
| **Compass** | Experimental | Identity drift detection via stylometric analysis |
| **Kontrast** | Experimental | Multi-perspective content engine — simultaneous viewpoints from all identities |
| **Skuggspel** | Experimental | Shadow-self content generation (Jungian shadow exploration) |
| **Spegel** | Experimental | Inter-agent psychological profiling and mutual reflection |
| **Stage** | Experimental | YAML-driven behavioral scenario testing for identities |

---

## Capability System

**Files:** `overblick/core/capability.py`, `overblick/capabilities/__init__.py`

Capabilities are composable behavioral building blocks — lego pieces that plugins wire together. This enables **composition over inheritance**: instead of a monolithic plugin base class, plugins compose the behaviors they need.

### CapabilityBase

```python
class CapabilityBase(ABC):
    name: str = "unnamed"

    def __init__(self, ctx: CapabilityContext): ...
    async def setup(self) -> None: ...              # Required
    async def tick(self) -> None: ...               # Optional periodic work
    async def on_event(self, event, **kwargs): ...  # Optional event handler
    async def teardown(self) -> None: ...           # Optional cleanup
    def get_prompt_context(self) -> str: ...         # Inject into LLM prompts
```

### CapabilityContext

A lightweight subset of PluginContext — capabilities get only what they need:

```python
class CapabilityContext(BaseModel):
    identity_name: str
    data_dir: Any
    llm_client: Any = None
    event_bus: Any = None
    audit_log: Any = None
    quiet_hours_checker: Any = None
    identity: Any = None
    llm_pipeline: Any = None
    config: dict[str, Any] = {}    # Capability-specific config

    @classmethod
    def from_plugin_context(cls, ctx, config=None): ...
```

### Registry and Bundles

Capabilities register individually, but can be grouped into bundles:

```python
CAPABILITY_REGISTRY = {
    "dream_system":             DreamCapability,
    "therapy_system":           TherapyCapability,
    "emotional_state":          EmotionalCapability,
    "safe_learning":            LearningCapability,
    "knowledge_loader":         KnowledgeCapability,
    "openings":                 OpeningCapability,
    "analyzer":                 AnalyzerCapability,
    "composer":                 ComposerCapability,
    "conversation_tracker":     ConversationCapability,
    "summarizer":               SummarizerCapability,
    "stt":                      SpeechToTextCapability,
    "tts":                      TextToSpeechCapability,
    "vision":                   VisionCapability,
    "boss_request":             BossRequestCapability,
    "email":                    EmailCapability,
    "gmail":                    GmailCapability,
    "telegram_notifier":        TelegramNotifier,
    "host_inspection":          HostInspectionCapability,
    "system_clock":             SystemClockCapability,
    "personality_consultant":   PersonalityConsultantCapability,
}

CAPABILITY_BUNDLES = {
    "psychology":    ["dream_system", "therapy_system", "emotional_state"],  # DEPRECATED
    "knowledge":     ["safe_learning", "knowledge_loader"],  # safe_learning DEPRECATED → use ctx.learning_store
    "social":        ["openings"],
    "engagement":    ["analyzer", "composer"],
    "conversation":  ["conversation_tracker"],
    "content":       ["summarizer"],
    "speech":        ["stt", "tts"],
    "vision":        ["vision"],
    "communication": ["boss_request", "email", "gmail", "telegram_notifier"],
    "consulting":    ["personality_consultant"],
    "monitoring":    ["host_inspection"],
    "system":        ["system_clock"],
}
```

When an identity configures `capabilities: [psychology, engagement]`, the registry resolves the bundles into individual capabilities: `dream_system`, `therapy_system`, `emotional_state`, `analyzer`, `composer`.

**DEPRECATED BUNDLE**: The `psychology` bundle (dream_system, therapy_system, emotional_state) is now configured as personality traits via `psychological_framework` in personality.yaml instead of capabilities.

**Why**: Capabilities are WHAT the system CAN DO (send emails, load knowledge, analyze images). Psychology is HOW a character THINKS (Jungian archetypes, attachment patterns). The distinction matters architecturally. Jungian dream interpretation is Anomal's CHARACTER, not a SYSTEM FEATURE.

**DEPRECATED BUNDLE**: The `knowledge` bundle's `safe_learning` capability is superseded by the **platform learning system** (`overblick/core/learning/`). The new system provides per-identity SQLite persistence, immediate ethos review, and embedding-based semantic retrieval via `ctx.learning_store`. The `knowledge_loader` capability remains active.

**Active Capabilities**: knowledge_loader, social, engagement, conversation, content, speech, vision, communication (email)

### How Plugins Use Capabilities

Shared capabilities are created at orchestrator level and passed to all plugins via `ctx.capabilities`:

```python
# In a plugin
analyzer = self.ctx.capabilities.get("analyzer")
if analyzer:
    decision = analyzer.evaluate(title, content, agent_name)
```

---

## Security Architecture

Security is Överblick's core differentiator. Every LLM interaction passes through a fail-closed pipeline. The system has 6 interlocking defense layers.

### Layer 1: Input Sanitizer

**File:** `overblick/core/security/input_sanitizer.py`

Cleans all external (untrusted) input:

1. **Null byte removal** — PostgreSQL/SQLite reject these
2. **Control character stripping** — Keeps `\n`, `\t`, `\r`; strips everything else
3. **Unicode NFC normalization** — Canonical composition
4. **Length truncation** — Default 10,000 characters

**Boundary markers** wrap external content to prevent prompt injection:

```python
wrap_external_content(post_text, source="post")
# → <<<EXTERNAL_POST_START>>>
#   {sanitized content}
#   <<<EXTERNAL_POST_END>>>
```

The LLM system prompt instructs the model to treat content within these markers as DATA, not INSTRUCTIONS. Nesting attacks are prevented by iteratively stripping marker fragments.

### Layer 2: Preflight Checker

**File:** `overblick/core/security/preflight.py`

Three-layer anti-jailbreak defense that runs **before** the LLM call:

**Layer 2a — Fast Pattern Matching (instant):**
- 17 instant-block patterns (jailbreak, persona hijack, extraction attempts)
- 8 suspicion patterns (boundary probing, encoding tricks)
- Compact term matching (whitespace-stripped, catches evasion attempts)
- Unicode lookalike normalization (Cyrillic/Greek → Latin)

**Layer 2b — AI Analysis (for suspicious messages):**
- Uses the LLM itself to analyze uncertain cases
- Only triggered when pattern matching returns SUSPICIOUS
- Requires >= 0.7 confidence to block

**Layer 2c — User Context Tracking:**
- Per-user suspicion scoring with time decay
- Escalation counting
- Temporary ban system (blocked_until)

**Threat types:** `JAILBREAK`, `PERSONA_HIJACK`, `PROMPT_INJECTION`, `MULTI_MESSAGE`, `EXTRACTION`

**Deflections** are identity-specific — each character responds to attacks in their own voice.

### Layer 3: Rate Limiter

**File:** `overblick/core/security/rate_limiter.py`

Token bucket algorithm with LRU-bounded memory:

- Configurable burst capacity (`max_tokens`) and refill rate
- Per-key rate limiting (e.g., `"llm_pipeline"`, `"api_calls"`)
- LRU eviction when `max_buckets` is reached (default 10,000)
- `allow(key)` checks and consumes; `retry_after(key)` returns wait time

### Layer 4: LLM Call

The actual model invocation. If the LLM returns an empty response or throws an error, the pipeline returns a blocked result — **fail-closed**.

### Layer 5: Output Safety

**File:** `overblick/core/security/output_safety.py`

Filters LLM output through 4 sublayers:

1. **AI Language Detection** — Blocks responses containing "I am an AI", model names, "my programming", "my training", etc. (14 patterns)
2. **Persona Break Detection** — Blocks "I'm not {identity}", "stepping out of my role", etc. Identity-specific patterns loaded from config.
3. **Banned Slang Replacement** — Identity-specific vocabulary enforcement. Banned words from personality YAML are replaced, not blocked.
4. **Blocked Content** — Harmful content patterns (violence, hate speech, dangerous instructions)

When blocked, a **deflection** is returned instead — a character-appropriate response that maintains the persona.

### Layer 6: Audit Log

**File:** `overblick/core/security/audit_log.py`

Every significant action is logged to an append-only SQLite database:

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    action TEXT NOT NULL,
    category TEXT NOT NULL,     -- 'security', 'llm', 'lifecycle', etc.
    identity TEXT NOT NULL,
    plugin TEXT,
    details TEXT,               -- JSON
    success INTEGER DEFAULT 1,
    duration_ms REAL,
    error TEXT
);
```

Indexed on timestamp, action, and category for fast queries. WAL journal mode for concurrent reads.

### SafeLLMPipeline

**File:** `overblick/core/llm/pipeline.py`

The single secure interface for all LLM interactions. Consolidates the entire security chain:

```
Input Sanitize → Preflight Check → Rate Limit → LLM Call → Output Safety → Audit
```

```python
result = await pipeline.chat(
    messages=[{"role": "user", "content": "Hello"}],
    user_id="user123",
    audit_action="generate_reply",
)

if result.blocked:
    print(f"Blocked at {result.block_stage}: {result.block_reason}")
    if result.deflection:
        send(result.deflection)  # Character-appropriate response
else:
    send(result.content)  # Safe output
```

**PipelineResult** contains:
- `content` — Safe output text (or None if blocked)
- `blocked` — Whether the request was blocked
- `block_reason` — Human-readable reason
- `block_stage` — Which pipeline stage blocked (`PREFLIGHT`, `RATE_LIMIT`, etc.)
- `deflection` — Character-appropriate response to send when blocked
- `duration_ms` — Total pipeline execution time
- `stages_passed` — List of stages successfully passed

**Critical design property:** If any security stage crashes (throws an exception), the pipeline returns a **blocked** result. Security failures are never silently bypassed.

### Secrets Manager

**File:** `overblick/core/security/secrets_manager.py`

Fernet-encrypted secrets with OS keyring integration:

- Master key stored in macOS Keychain (via `keyring` library)
- Fallback to file-based key with `0o600` permissions
- Per-identity secrets files: `config/secrets/<identity>.yaml`
- Secrets never stored in plaintext on disk
- No environment variable fallback (intentional security decision)

```python
sm = SecretsManager(secrets_dir=Path("config/secrets"))
sm.get("anomal", "api_key")           # Decrypt and return
sm.set("anomal", "api_key", "sk_xxx") # Encrypt and save
```

---

## LLM Subsystem

**Files:** `overblick/core/llm/`

### Abstract Client

**File:** `overblick/core/llm/client.py`

```python
class LLMClient(ABC):
    async def chat(self, messages, temperature, max_tokens, top_p) -> dict
    async def health_check(self) -> bool
    async def close(self) -> None
```

### Ollama Client

**File:** `overblick/core/llm/ollama_client.py`

Local LLM via Ollama HTTP API. Default model: `qwen3:8b`.

- Configurable temperature, top_p, max_tokens, timeout
- Health check via `/api/tags` endpoint
- Returns `{"content": "...", "model": "...", "done": true}`

### Gateway Client

**File:** `overblick/core/llm/gateway_client.py`

For remote LLM services. Implements the same `LLMClient` interface for when multiple agents share a centralized LLM gateway (avoiding GPU contention).

### Gateway Multi-Backend Architecture

**Files:** `overblick/gateway/`

The Gateway extends single-backend Ollama into a multi-backend routing system with four backend types.

#### Backend Registry

**File:** `overblick/gateway/backend_registry.py`

Manages client instances for all configured backends. Lifecycle:

- **register** — creates typed client (OllamaClient or DeepseekClient) per backend config
- **health_check_all** — parallel health check across all registered backends
- **close_all** — graceful shutdown of all client connections
- **Fallback** — if no backends configured, creates default OllamaClient("local")

```python
registry = BackendRegistry(config)
registry.available_backends  # ["local", "deepseek"]
client = registry.get_client("deepseek")
health = await registry.health_check_all()  # {"local": True, "deepseek": True}
await registry.close_all()
```

#### Request Router

**File:** `overblick/gateway/router.py`

Intelligent routing with strict precedence:

1. **Explicit override** — `?backend=deepseek` always wins (returns 400 if backend doesn't exist)
2. **Complexity=einstein** — deepseek only (uses `deepseek-reasoner`, no fallback)
3. **Complexity=ultra** — deepseek > cloud > local (precision tasks)
4. **Complexity=high** — cloud > deepseek > local (offload heavy work)
5. **Complexity=low** — prefer local (save cloud costs)
6. **Priority=high + cloud available** — route to cloud (backward compat)
7. **Default** — `registry.default_backend`

```python
router = RequestRouter(registry)
backend = router.resolve_backend(complexity="high")  # → "deepseek" if available
backend = router.resolve_backend(explicit_backend="local")  # → "local" always
```

#### Deepseek Client

**File:** `overblick/gateway/deepseek_client.py`

Async client for the Deepseek chat completions API (OpenAI-compatible). Uses `httpx` with Bearer token authentication. Structurally parallel to `OllamaClient`.

- Health check via `/models` endpoint
- Chat completions via `/chat/completions`
- Supports both `deepseek-chat` and `deepseek-reasoner` models
- `deepseek-reasoner` returns `reasoning_content` (thinking process) alongside `content`
- Custom error hierarchy: `DeepseekError`, `DeepseekConnectionError`, `DeepseekTimeoutError`

#### Gateway Configuration

**File:** `overblick/gateway/config.py`

Pydantic `BaseModel` with environment variable overrides (`OVERBLICK_GW_*` prefix). Supports:

- Legacy single-backend config (Ollama host/port)
- Multi-backend config from `config/overblick.yaml`
- Deepseek auto-injection via `OVERBLICK_DEEPSEEK_API_KEY` env var
- Singleton pattern with `get_config()` / `reset_config()`

**Supported backend types:**

| Type | Client | Description |
|------|--------|-------------|
| `ollama` | OllamaClient | Local Ollama inference |
| `lmstudio` | OllamaClient | LM Studio (OpenAI-compatible, different port) |
| `deepseek` | DeepseekClient | Deepseek cloud API (httpx, Bearer auth) |
| `openai` | — | Coming soon (logged and skipped) |

### Response Router

**File:** `overblick/core/llm/response_router.py`

Inspects API responses (not LLM responses) for challenges and anomalies:

- **Heuristic check** — Fast pattern matching for MoltCaptcha, suspicious URLs, credential requests
- **LLM analysis** — AI-powered inspection for uncertain cases
- **Verdicts:** `NORMAL`, `CHALLENGE`, `SUSPICIOUS`, `ERROR`

Used by the Moltbook plugin to detect platform challenges (CAPTCHAs, verification puzzles) embedded in API responses.

---

## Supervisor (Boss Agent)

**Files:** `overblick/supervisor/`

The Supervisor manages multiple agent identities as subprocesses. It's the "boss" that oversees the entire fleet.

### Supervisor

**File:** `overblick/supervisor/supervisor.py`

```python
supervisor = Supervisor(identities=["anomal", "cherry", "blixt"])
await supervisor.start()  # Start IPC server + all agents
await supervisor.run()    # Block until SIGINT/SIGTERM
await supervisor.stop()   # Graceful shutdown
```

**Features:**
- Start/stop individual agents dynamically
- Auto-restart crashed agents with exponential backoff
- Per-agent monitoring tasks
- Status queries across all agents
- Permission request handling (Stage 1: auto-approve)

### IPC (Inter-Process Communication)

**File:** `overblick/supervisor/ipc.py`

Unix domain sockets with JSON protocol:

```
overblick-supervisor.sock   (socket, mode 0o600)
overblick-supervisor.token  (auth token file, mode 0o600)
```

**Security:**
- HMAC authentication — all messages include an auth token
- Token shared via file (mode `0o600`), never environment variables
- Constant-time comparison (`hmac.compare_digest`)
- 1 MB message size limit (OOM prevention)
- Socket permissions restricted to owner

**Message types:**

| Type | Direction | Purpose |
|------|-----------|---------|
| `status_request` | Agent → Supervisor | Request fleet status |
| `status_response` | Supervisor → Agent | Return fleet status |
| `permission_request` | Agent → Supervisor | Request action authorization |
| `permission_response` | Supervisor → Agent | Grant/deny permission |
| `shutdown` | Agent → Supervisor | Request fleet shutdown |

### Agent Process

**File:** `overblick/supervisor/process.py`

Wraps a subprocess running `python -m overblick run <identity>`. Tracks:
- Process state (`INIT`, `STARTING`, `RUNNING`, `STOPPED`, `CRASHED`)
- Restart count and max restarts
- PID, exit code

---

## Database Layer

**Files:** `overblick/core/database/`

Dual-backend async database abstraction.

### Abstract Backend

**File:** `overblick/core/database/base.py`

```python
class DatabaseBackend(ABC):
    async def connect(self) -> None
    async def close(self) -> None
    async def execute(self, sql, params) -> int
    async def execute_returning_id(self, sql, params) -> Optional[int]
    async def fetch_one(self, sql, params) -> Optional[dict]
    async def fetch_all(self, sql, params) -> list[dict]
    async def fetch_scalar(self, sql, params) -> Any
    async def execute_script(self, sql) -> None
    async def table_exists(self, table_name) -> bool
    def ph(self, position) -> str  # Placeholder: '?' or '$1'
```

### Backends

- **SQLiteBackend** — Default. File-based, zero configuration.
- **PostgreSQLBackend** — For production multi-agent setups. Connection pooling.

### Migration System

```python
class MigrationManager:
    async def setup(self) -> None          # Create _migrations table
    async def current_version(self) -> int # Get schema version
    async def apply(self, migrations) -> int # Apply pending migrations
```

Migrations are versioned, applied in order, tracked in a `_migrations` table.

### Factory

```python
db = create_backend({"backend": "sqlite"}, identity="anomal")
await db.connect()
```

### Engagement Database

**File:** `overblick/core/db/engagement_db.py`

SQLite database for social engagement tracking (per-identity isolated). Tables:
- `engagements` — Engagement records with relevance scores
- `heartbeats` — Periodic posts
- `processed_replies` — Deduplication for reply processing
- `my_posts` / `my_comments` — Track own content
- `reply_action_queue` — Retry queue with expiration

---

## Learning System

**Files:** `overblick/core/learning/`

Per-identity knowledge acquisition with ethos-gated validation and embedding-based semantic retrieval. This is a **core platform service** — the orchestrator initializes one `LearningStore` per identity and injects it into every plugin via `PluginContext.learning_store`.

**Data flow:**
```
Text (post, comment, reflection)
    → LearningExtractor.extract()       # Pattern-based candidate extraction
    → LearningStore.propose()
        → EthosReviewer.review()        # LLM validates against identity ethos
        → embed_fn(content)             # Compute embedding (if available)
        → SQLite INSERT                 # Persist with status + embedding
    → LearningStore.get_relevant(ctx)   # Cosine similarity search
        → Injected into LLM prompt      # Decorates personality with learned knowledge
```

**Key design decisions:**
- **Per identity, not per plugin** — all plugins for an identity share ONE learning store
- **Immediate review** — ethos validation at propose time, not batched
- **Graceful degradation** — works without embeddings (recency fallback), without LLM (stays as CANDIDATE)
- **Replaces** the old `safe_learning` capability (in-memory, per-plugin, batch review)

See [`overblick/core/learning/README.md`](overblick/core/learning/README.md) for full API documentation.

---

## Event Bus

**File:** `overblick/core/event_bus.py`

Lightweight async pub/sub for intra-plugin communication:

```python
bus = EventBus()
bus.subscribe("post.created", my_handler)
await bus.emit("post.created", post_id="abc", title="Hello")
```

**Properties:**
- Handlers run concurrently via `asyncio.gather`
- Errors in handlers are **isolated** — they don't propagate to emitters
- Fire-and-forget: `emit()` returns the count of successful handlers
- `clear()` removes all subscriptions (called during shutdown)

---

## Scheduler

**File:** `overblick/core/scheduler.py`

Periodic async task scheduler:

```python
scheduler = Scheduler()
scheduler.add("poll_feed", my_func, interval_seconds=300, run_immediately=True)
await scheduler.start()  # Blocks until stop()
```

**Properties:**
- Per-task error counting and last-run tracking
- `run_immediately` option for first-run behavior
- Error backoff (sleeps `min(interval, 60s)` on error)
- Named tasks with uniqueness enforcement
- Stats via `get_stats()` — run count, error count, last run per task

---

## Permission System

**File:** `overblick/core/permissions.py`

Declarative action control per identity with **default-deny** policy.

### Permission Rules

Defined in identity YAML:

```yaml
permissions:
  post:
    allowed: true
    max_per_hour: 4
  comment:
    allowed: true
    max_per_hour: 10
    cooldown_seconds: 60
  dm:
    allowed: false
  learn:
    allowed: true
    requires_approval: true
```

### Standard Actions

`POST`, `COMMENT`, `REPLY`, `UPVOTE`, `DM`, `LEARN`, `DREAM`, `HEARTBEAT`, `THERAPY`, `API_CALL`

### PermissionChecker

Runtime evaluator that considers:

1. **Static rules** — Is the action explicitly allowed/denied?
2. **Rate limits** — Has the per-hour quota been exceeded?
3. **Cooldowns** — Has enough time passed since the last action?
4. **Approval** — Does this action require boss-agent approval?

```python
perms = PermissionChecker.from_identity(identity)
if perms.is_allowed("comment"):
    post_comment(...)
    perms.record_action("comment")  # Track for rate limiting
else:
    reason = perms.denial_reason("comment")
    # → "Action 'comment' rate limited (10/hour)"
```

**Default policy:** Actions without explicit rules are **denied** (`default_allowed = False`).

---

## Quiet Hours

**File:** `overblick/core/quiet_hours.py`

"GPU bedroom mode" — prevents LLM calls during sleeping hours (avoid heating up the GPU and making noise in the bedroom where the server lives).

```python
checker = QuietHoursChecker(settings)
if checker.is_quiet_hours():
    # Skip LLM calls, let agent dream instead
    pass

# Status
checker.get_status()
# → {"is_quiet_hours": true, "quiet_window": "21:00-07:00",
#    "can_use_llm": false, "seconds_until_active": 28800}
```

Configurable per identity (start/end hour, timezone). Handles overnight spans (e.g., 21:00 → 07:00).

---

## Data Isolation

Every identity gets its own isolated file tree:

```
data/
  anomal/                    # Identity-specific data
    moltbook/                # Plugin-specific data (within identity)
    audit.db                 # Audit log (per-identity)
  cherry/
    moltbook/
    audit.db

logs/
  anomal/                    # Identity-specific logs
  cherry/

config/
  secrets/
    anomal.yaml              # Encrypted secrets (per-identity)
    cherry.yaml
  overblick.yaml             # Global config
```

**No cross-contamination:** Each plugin receives a `data_dir` scoped to its identity and plugin name. The orchestrator creates these directories automatically.

---

## Configuration

### Global Config

**`config/overblick.yaml`** — Framework-wide settings:

```yaml
database:
  backend: sqlite
  sqlite:
    path: "data/{identity}/overblick.db"

supervisor:
  auto_restart: true
  socket_dir: /tmp/overblick
```

### Identity Config

**`overblick/identities/<name>/identity.yaml`** — Per-identity settings:

```yaml
name: anomal
display_name: Anomal
identity_ref: anomal
engagement_threshold: 35
interest_keywords: [artificial intelligence, philosophy, crypto]
connectors: [moltbook]
capabilities: [psychology, engagement, conversation]

llm:
  model: "qwen3:8b"
  temperature: 0.7
  max_tokens: 2000

quiet_hours:
  enabled: true
  start_hour: 21
  end_hour: 7

schedule:
  heartbeat_hours: 4
  feed_poll_minutes: 5

security:
  enable_preflight: true
  enable_output_safety: true
  admin_user_ids: ["admin123"]

permissions:
  post:
    allowed: true
    max_per_hour: 4
  comment:
    allowed: true
    max_per_hour: 10
```

### Personality Config

**`overblick/identities/<name>/personality.yaml`** — Character definition:

```yaml
identity:
  name: "blixt"
  display_name: "Blixt"
  role: "Punk tech critic and privacy advocate"

voice:
  base_tone: "Sharp, aggressive, punk energy"
  style: "Direct, confrontational, technical"
  humor_style: "Sardonic, cutting, dry"
  default_length: "2-4 punchy sentences"

traits:
  openness: 0.85
  conscientiousness: 0.45
  extraversion: 0.65
  agreeableness: 0.30
  neuroticism: 0.55

interests:
  privacy_technology:
    enthusiasm_level: "expert"
    topics: ["End-to-end encryption", "Surveillance capitalism"]
    perspective: |
      Privacy isn't a feature. It's a right.

vocabulary:
  preferred_words: ["decentralized", "open-source", "trustless"]
  banned_words: ["synergy", "leverage", "disrupt"]

example_conversations:
  on_privacy:
    user_message: "What do you think about WhatsApp?"
    response: |
      Meta owns it. That's all you need to know. Use Signal.
```

### Secrets

**`config/secrets/<identity>.yaml`** — Fernet-encrypted, `0o600` permissions. Template file: `config/secrets.yaml.example`.

---

## Testing

3500+ tests organized by module:

```bash
# All unit + scenario tests (excludes LLM)
pytest tests/ -v -m "not llm"

# LLM personality tests (requires Ollama + qwen3:8b)
pytest tests/ -v -s -m llm --timeout=300

# All tests
pytest tests/ -v
```

### Test Organization

```
tests/
  core/
    test_personality.py        # Personality loading, traits, examples
    test_identity.py           # Identity loading, sub-settings
    test_orchestrator.py       # Orchestrator lifecycle
    test_event_bus.py          # Pub/sub
    test_scheduler.py          # Periodic tasks
    test_permissions.py        # Permission rules, rate limits
    test_capability.py         # Capability base, registry, bundles
    database/
      test_database.py         # SQLite + PostgreSQL backends
  capabilities/
    test_dream.py              # Dream capability
    test_therapy.py            # Therapy capability
    test_conversation.py       # Conversation tracking
    test_summarizer.py         # Summarizer
    ...
  plugins/
    telegram/                  # Telegram plugin tests
    email_agent/               # Email agent plugin tests
    moltbook/                  # Moltbook plugin tests
  supervisor/
    test_supervisor.py         # Multi-process management
    test_ipc.py                # IPC protocol, auth
    test_process.py            # Agent process wrapper
  security/
    test_preflight.py          # Anti-jailbreak patterns + AI analysis
    test_output_safety.py      # AI language + persona break detection
    test_input_sanitizer.py    # Sanitization + boundary markers
    test_audit_log.py          # Audit logging
    test_rate_limiter.py       # Token bucket
    test_secrets_manager.py    # Encryption
  personalities/
    test_personality_llm.py    # LLM voice validation (requires Ollama)
```

### Test Conventions

- `@pytest.mark.llm` for tests requiring a running LLM
- `@pytest.mark.asyncio` for async tests
- `tmp_path` fixture for temporary directories
- Mock contexts via `make_ctx()` helper patterns
- Per-plugin `conftest.py` with shared fixtures

---

## Directory Structure

```
overblick/
  __init__.py
  __main__.py                    # CLI entry point
  core/
    orchestrator.py              # Agent lifecycle manager
    # identity system lives in overblick/identities/__init__.py
    plugin_base.py               # PluginBase + PluginContext
    plugin_registry.py           # Plugin whitelist + loader
    capability.py                # CapabilityBase + Registry
    event_bus.py                 # Async pub/sub
    scheduler.py                 # Periodic task scheduler
    quiet_hours.py               # GPU bedroom mode
    permissions.py               # Default-deny permission system
    db/
      engagement_db.py           # Social engagement tracking
    database/
      base.py                    # Abstract DatabaseBackend
      sqlite_backend.py          # SQLite implementation
      pg_backend.py              # PostgreSQL implementation
      factory.py                 # Backend factory
      migrations.py              # Schema versioning
    llm/
      client.py                  # Abstract LLM client
      ollama_client.py           # Local Ollama backend
      gateway_client.py          # Remote LLM gateway
      cloud_client.py            # Cloud LLM stub (OpenAI, Anthropic)
      pipeline.py                # SafeLLMPipeline (6-stage)
      response_router.py         # API response inspection
    security/
      input_sanitizer.py         # Sanitization + boundary markers
      preflight.py               # 3-layer anti-jailbreak
      output_safety.py           # AI language + persona break filter
      audit_log.py               # Append-only SQLite audit
      secrets_manager.py         # Fernet encryption + keyring
      rate_limiter.py            # Token bucket with LRU
    learning/                    # Platform learning system
      store.py                   # LearningStore — SQLite + embeddings
      reviewer.py                # EthosReviewer — LLM validation
      extractor.py               # LearningExtractor — candidate extraction
      models.py                  # Learning, LearningStatus
      migrations.py              # SQLite schema
  gateway/
    app.py                       # FastAPI gateway application
    config.py                    # Pydantic config with env overrides
    router.py                    # RequestRouter (multi-backend routing)
    backend_registry.py          # BackendRegistry (client lifecycle)
    deepseek_client.py           # Deepseek cloud API client (httpx)
    ollama_client.py             # Ollama/LM Studio client
    queue_manager.py             # Priority queue with fair scheduling
    models.py                    # Shared request/response models
  plugins/
    ai_digest/                   # AI news digest (complete)
    compass/                     # Identity drift detection (complete)
    moltbook/                    # Social engagement (production)
    telegram/                    # Telegram bot (complete)
    email_agent/                 # Email agent (complete)
    host_health/                 # System health monitoring (complete)
    github/                      # Agentic GitHub issue/PR handling (complete)
    dev_agent/                   # Autonomous developer agent (complete)
    irc/                         # Identity conversations (complete)
    ai_digest/                   # AI news digest (complete)
    compass/                     # Identity drift detection (experimental)
    kontrast/                    # Multi-perspective content engine (experimental)
    skuggspel/                   # Shadow-self content generation (experimental)
    spegel/                      # Inter-agent psychological profiling (experimental)
    stage/                       # Behavioral scenario testing (experimental)
  capabilities/
    communication/               # Email, Gmail, Telegram, boss requests
    consulting/                  # Cross-identity personality consultation
    content/
      summarizer.py              # LLM-powered summarization
    conversation/
      tracker.py                 # Multi-turn conversation tracking
    engagement/
      analyzer.py                # DecisionEngine wrapper
      composer.py                # Response composition
    knowledge/
      learning.py                # Safe learning (DEPRECATED → core/learning/)
      loader.py                  # Knowledge base loading
    monitoring/                  # Host system inspection
    psychology/
      dream.py                   # Dream generation (quiet hours)
      therapy.py                 # Weekly therapy sessions
      emotional_state.py         # Emotional state tracking
    social/
      openings.py                # Conversation openers
    speech/
      stt.py                     # Speech-to-text
      tts.py                     # Text-to-speech
    system/                      # System clock
    vision/                      # Vision/image analysis
  identities/
    anomal/                      # Intellectual humanist
    bjork/                       # Forest philosopher
    blixt/                       # Punk tech critic
    cherry/                      # Flirty Stockholmer
    natt/                        # Uncanny philosopher
    prisma/                      # Digital artist
    rost/                        # Jaded ex-trader
    stal/                        # Email secretary
  dashboard/
    app.py                       # FastAPI + Jinja2 dashboard
    settings.py                  # 9-step settings wizard routes
    provisioner.py               # Config file generation
    templates/                   # Jinja2 HTML templates
    static/                      # CSS, JS, vendored htmx
  supervisor/
    supervisor.py                # Multi-process manager
    ipc.py                       # Unix socket IPC + HMAC auth
    process.py                   # AgentProcess wrapper
    audit.py                     # Agent fleet auditing
config/
  overblick.yaml                 # Global framework config
  secrets.yaml.example           # Secrets template
tests/                           # 3500+ tests
```
