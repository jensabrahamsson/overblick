# Överblick

Security-focused multi-identity agent framework. Python 3.13+. GPL v3.

Överblick consolidates multiple AI agent identities into a single codebase with a plugin architecture. Each identity operates with a distinct **personality** — voice, interests, traits, and behavioral constraints — all driven by YAML configuration. The framework emphasizes security at every layer: a 6-stage LLM pipeline, encrypted secrets, structured audit logging, prompt injection boundaries, and default-deny permissions.

## Quick Start

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick
python3.13 -m venv venv
source venv/bin/activate

# Option A: Install as editable package (recommended for development)
pip install -e ".[dev]"

# Option B: Install dependencies only
pip install -r requirements.txt        # core only
pip install -r requirements-dev.txt    # core + test/dev tools

# Run tests (796 unit + scenario tests)
pytest tests/ -v

# Run LLM personality tests (requires Ollama with qwen3:8b)
pytest tests/ -v -m llm --timeout=300

# Run a specific identity
python -m overblick run anomal
```

## Architecture

```
                     ┌───────────────┐
                     │  Supervisor   │  Boss agent: process management,
                     │  (Boss Agent) │  audit, permission decisions
                     └──────┬────────┘
                            │ IPC (Unix sockets, HMAC auth)
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼──────┐ ┌───▼─────┐ ┌────▼──────┐
        │  Identity   │ │Identity │ │ Identity  │
        │  "anomal"   │ │"cherry" │ │  "volt"   │
        └─────┬──────┘ └───┬─────┘ └────┬──────┘
              │             │             │
        ┌─────▼─────────────▼─────────────▼──────┐
        │           Plugin Layer                  │
        │  Moltbook │ Telegram │ Gmail │ RSS │ …  │
        └─────┬─────────────────────────┬────────┘
              │                         │
        ┌─────▼───────┐          ┌──────▼──────┐
        │ SafeLLM     │          │  Security   │
        │ Pipeline    │          │  Layer      │
        │ (6 stages)  │          │             │
        └─────────────┘          └─────────────┘
```

### SafeLLM Pipeline (6 stages)

Every LLM interaction passes through:

1. **Input Sanitize** — Strip null bytes, control chars, normalize unicode
2. **Preflight Check** — Detect jailbreak/injection attempts
3. **Rate Limit** — Token bucket throttling per identity
4. **LLM Call** — Invoke the language model (Ollama, Gateway)
5. **Output Safety** — Filter AI leakage, persona breaks, blocked content
6. **Audit Log** — Record the interaction for review

Pipeline is **fail-closed**: if any security stage crashes, the request is blocked (not passed through).

## Personality Stable

Personalities define WHO the agent IS — separate from operational config. Each personality can optionally have a psychological framework (Jungian, Attachment Theory, Stoic, Existential) that shapes how they think, not just what they can do.

| Personality | Voice | Style |
|-------------|-------|-------|
| **Anomal** | Intellectual humanist (James May energy) | Measured, curious, cross-domain parallels |
| **Cherry** | 28yo Stockholm woman | Flirty, emoji-heavy, Swedish pop culture |
| **Volt** | Punk tech critic | Sharp, aggressive, anti-corporate, privacy-obsessed |
| **Birch** | Forest philosopher | Sparse, calm, nature metaphors, Swedish stoicism |
| **Prism** | Digital artist | Colorful, synesthetic, warm, encouraging |
| **Rust** | Jaded ex-trader | Cynical, dark humor, cautionary tales |
| **Nyx** | Uncanny philosopher | Eerie, paradoxical, recursive, existential |

### Creating a New Personality

1. Create `overblick/identities/<name>/personality.yaml`
2. Define: identity, voice, traits, interests, vocabulary, examples
3. Test with the LLM test suite:

```bash
# Add your personality to the test parametrize lists
pytest tests/personalities/test_personality_llm.py -v -s -m llm --timeout=300
```

**Personality YAML structure:**

```yaml
identity:
  name: "mybot"
  display_name: "MyBot"
  role: "Helpful assistant"
  description: "A friendly AI helper"

voice:
  base_tone: "Warm and conversational"
  style: "Clear, direct, uses analogies"
  default_length: "2-4 sentences"

traits:
  warmth: 0.8
  helpfulness: 0.9
  humor: 0.5

vocabulary:
  preferred_words: ["consider", "interesting", "perhaps"]
  banned_words: ["synergy", "leverage", "disrupt"]

example_conversations:
  greeting:
    user_message: "Hello!"
    response: "Hey there! What's on your mind today?"
```

The personality system loads from three locations (in order):
1. `overblick/identities/<name>/personality.yaml` (directory-based, preferred)
2. `overblick/identities/<name>.yaml` (standalone file)
3. `overblick/personalities/<name>/personality.yaml` (legacy location)

## Plugin System

Plugins are self-contained modules. Each receives `PluginContext` as its ONLY framework interface.

**Available plugins:**

| Plugin | Status | Description |
|--------|--------|-------------|
| **Moltbook** | Production | Autonomous social engagement (OBSERVE → THINK → DECIDE → ACT → LEARN) |
| **Telegram** | Complete | Bot with commands, conversation tracking, rate limiting |
| **Gmail** | Complete | Email processing, draft mode, boss agent approval workflow |
| **Discord** | Shell | Bot with guild/channel management (community contribution welcome) |
| **RSS** | Shell | Feed monitoring with keyword filtering (community contribution welcome) |
| **Webhook** | Shell | HTTP endpoint for external integrations (community contribution welcome) |
| **Matrix** | Shell | Decentralized chat with E2EE support (community contribution welcome) |

**Plugin lifecycle:**

```python
class MyPlugin(PluginBase):
    name = "myplugin"

    async def setup(self) -> None:
        """Initialize using self.ctx (PluginContext)."""
        token = self.ctx.get_secret("my_token")
        ...

    async def tick(self) -> None:
        """Called periodically by the scheduler."""
        result = await self.ctx.llm_pipeline.chat(messages=[...])
        if result.blocked:
            logger.warning("Blocked: %s", result.block_reason)
        ...

    async def teardown(self) -> None:
        """Cleanup on shutdown."""
```

**PluginContext provides:**

| Service | Description |
|---------|-------------|
| `identity` | Frozen Identity dataclass with all config |
| `llm_pipeline` | SafeLLMPipeline (preferred — includes all security) |
| `llm_client` | Raw LLM client (use pipeline instead) |
| `audit_log` | Structured audit logging |
| `event_bus` | Pub/sub event system |
| `scheduler` | Periodic task scheduling |
| `permissions` | Permission checker (default-deny) |
| `get_secret(key)` | Fernet-encrypted secrets access |

## Supervisor (Boss Agent)

The supervisor manages multiple agent identities as subprocesses:

```python
supervisor = Supervisor(identities=["anomal", "cherry", "volt"])
await supervisor.start()   # Start all agents
await supervisor.run()     # Block until shutdown
```

**Features:**
- Process lifecycle management (start, stop, auto-restart with backoff)
- IPC via Unix domain sockets with HMAC authentication
- Agent audit system (health, performance, safety, rate limit monitoring)
- Permission request handling (auto-approve in stage 1)
- Trend analysis across audit history

## Security Architecture

**6-layer defense:**

1. **Input Sanitizer** — Strips null bytes, control chars, normalizes unicode
2. **Boundary Markers** — External content wrapped in `<<<EXTERNAL_*_START>>>` / `<<<EXTERNAL_*_END>>>` markers
3. **Preflight Checker** — Blocks jailbreak/injection attempts before LLM call
4. **SafeLLM Pipeline** — All security stages in one pipeline, fail-closed
5. **Output Safety** — Scans LLM responses for AI leakage and persona breaks
6. **Audit Log** — Every action logged with structured JSON

**Additional protections:**
- `SecretsManager` — Fernet-encrypted per-identity secrets
- `RateLimiter` — Token bucket rate limiting
- `PermissionChecker` — Default-deny permission system
- IPC authentication — HMAC-signed messages between processes

## Identity System

Each identity is a frozen dataclass loaded from YAML:

```yaml
# overblick/identities/anomal/identity.yaml
name: anomal
display_name: Anomal
engagement_threshold: 35.0
interest_keywords: [artificial intelligence, crypto, philosophy]
enabled_modules: [dream_system, therapy_system, safe_learning]

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
```

## Database

Dual-backend database abstraction:

- **SQLite** — Default for development and single-agent deployment
- **PostgreSQL** — For production multi-agent setups

Both backends share the same migration system and API.

## Testing

```bash
# All unit + scenario tests (796)
pytest tests/ -v

# LLM personality tests (requires Ollama + qwen3:8b)
pytest tests/ -v -m llm --timeout=300

# Specific plugin
pytest tests/plugins/telegram/ -v
pytest tests/plugins/gmail/ -v
pytest tests/plugins/moltbook/ -v

# Supervisor tests
pytest tests/supervisor/ -v

# Personality tests (non-LLM)
pytest tests/personalities/ -v --ignore=tests/personalities/test_personality_llm.py

# With coverage
pytest tests/ --cov=overblick
```

## Directory Structure

```
overblick/
  core/
    orchestrator.py         # Main agent loop coordinator
    identity.py             # YAML → frozen Identity dataclass
    plugin_base.py          # PluginBase ABC + PluginContext
    plugin_registry.py      # Plugin discovery and registration
    event_bus.py            # Pub/sub event system
    scheduler.py            # Periodic task scheduler
    quiet_hours.py          # Time-based activity gating
    db/
      backend.py            # DatabaseBackend ABC
      sqlite_backend.py     # SQLite implementation
      postgres_backend.py   # PostgreSQL implementation
      migrations.py         # Migration system
      engagement_db.py      # Engagement tracking
    llm/
      client.py             # Abstract LLM client
      ollama_client.py      # Ollama backend
      gateway_client.py     # LLM Gateway backend
      pipeline.py           # SafeLLMPipeline (6-stage security chain)
      response_router.py    # LLM response inspection
    security/
      preflight.py          # Pre-posting content checks
      output_safety.py      # Post-generation content safety
      input_sanitizer.py    # Input sanitization + boundary markers
      audit_log.py          # Structured audit logging
      secrets_manager.py    # Fernet-encrypted secrets
      rate_limiter.py       # Token bucket rate limiting
    permissions/
      model.py              # Permission, PermissionSet dataclasses
      checker.py            # PermissionChecker (default-deny)
      store.py              # YAML permission store
  plugins/
    moltbook/               # Autonomous social engagement (production)
    telegram/               # Telegram bot (complete)
    gmail/                  # Email agent (complete)
    discord/                # Discord bot (shell)
    rss/                    # RSS feed monitor (shell)
    webhook/                # HTTP webhook receiver (shell)
    matrix/                 # Matrix chat (shell)
  personalities/            # Reusable personality stable
    anomal/                 # Intellectual humanist
    cherry/                 # Flirty Stockholmer
    volt/                   # Punk tech critic
    birch/                  # Forest philosopher
    prism/                  # Digital artist
    rust/                   # Jaded ex-trader
    nyx/                    # Uncanny philosopher
  identities/
    anomal/                 # Identity config + knowledge files
    cherry/                 # Identity config + knowledge files
  supervisor/
    supervisor.py           # Multi-process manager (Boss Agent)
    ipc.py                  # IPC layer (Unix sockets + HMAC)
    process.py              # AgentProcess wrapper
    audit.py                # Agent audit system
config/
  overblick.yaml            # Global framework config
tests/                      # 796+ unit + scenario + LLM tests
```

## Configuration

**`config/overblick.yaml`** — Global settings
**`config/secrets.yaml`** — Fernet-encrypted secrets (gitignored)
**`config/permissions.yaml`** — Default-deny permission grants (gitignored)

Template files (`*.yaml.example`) are checked into the repo.

## Claude Code Skills

Överblick ships with four Claude Code skills in `.claude/skills/` that accelerate framework development:

| Skill | Triggers | Purpose |
|-------|----------|---------|
| **overblick-skill-compiler** | "compile skill", "build from spec", "generate plugin" | Compile specs into full production-grade plugins, capabilities, and personalities with tests and registry wiring |
| **overblick-plugin-helper** | "create plugin", "review plugin", "debug plugin" | Interactive guidance for plugin development, security checklist, PluginBase/PluginContext API |
| **overblick-capability-helper** | "create capability", "add capability", "review capability" | Interactive guidance for composable behavioral blocks, bundles, CapabilityBase/Registry API |
| **overblick-personality-helper** | "create personality", "design character", "review personality" | Interactive guidance for YAML-driven characters with psychological trait models, voice design |

Each skill includes reference documentation covering the full API, real examples, and checklists. The **skill compiler** automates end-to-end component generation; the helpers provide interactive guidance. Skills are loaded automatically by Claude Code when matching triggers are detected.

## Contributing

Överblick uses shell plugins as entry points for community contributions. Each shell implements the `PluginBase` interface with detailed TODO comments explaining what needs to be built.

**Good first contributions:**
- Implement the Discord plugin (needs `discord.py`)
- Implement the RSS plugin (needs `feedparser`)
- Implement the Matrix plugin (needs `matrix-nio`)
- Create a new personality for the stable
- Add chaos tests (`tests/chaos/`)

## License

GPL v3. See [LICENSE](LICENSE) for details.
