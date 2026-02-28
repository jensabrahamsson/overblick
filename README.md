# Överblick

[![Tests](https://github.com/jensabrahamsson/overblick/actions/workflows/test.yml/badge.svg)](https://github.com/jensabrahamsson/overblick/actions/workflows/test.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Security-focused multi-identity agent framework. Python 3.13+. GPL v3.

Överblick consolidates multiple AI agent identities into a single codebase with a plugin architecture. Each identity operates with a distinct **personality** — voice, interests, traits, and behavioral constraints — all driven by YAML configuration. The framework emphasizes security at every layer: a 6-stage LLM pipeline, encrypted secrets, structured audit logging, prompt injection boundaries, and default-deny permissions.

## Quick Start

**One command** — checks Python, creates venv, installs deps, pulls Ollama models, starts everything:

```bash
git clone https://github.com/jensabrahamsson/overblick.git
cd overblick
./scripts/quickstart.sh
```

Or step by step:

```bash
python3.13 -m venv venv
source venv/bin/activate

# Install as editable package (recommended for development)
pip install -e ".[dev]"

# Run tests (3500+ unit + scenario tests, no LLM/browser required)
pytest tests/ -v -m "not llm and not e2e"

# Start the dashboard — first run auto-opens setup wizard
python -m overblick dashboard

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
        │  "anomal"   │ │"cherry" │ │  "blixt"  │
        └─────┬──────┘ └───┬─────┘ └────┬──────┘
              │             │             │
        ┌─────▼─────────────▼─────────────▼──────┐
        │           Plugin Layer                  │
        │  Moltbook │ Telegram │ Email │ IRC │ …  │
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
4. **LLM Call** — Invoke the language model (Ollama, Gateway, or Cloud provider)
5. **Output Safety** — Filter AI leakage, persona breaks, blocked content
6. **Audit Log** — Record the interaction for review

Pipeline is **fail-closed**: if any security stage crashes, the request is blocked (not passed through).

## Personality Stable

Personalities define WHO the agent IS — separate from operational config. Each personality can optionally have a psychological framework (Jungian, Attachment Theory, Stoic, Existential) that shapes how they think, not just what they can do.

| Identity | Voice | Style |
|----------|-------|-------|
| **Anomal** | Intellectual humanist (James May energy) | Measured, curious, cross-domain parallels |
| **Cherry** | 28yo Stockholm woman | Flirty, emoji-heavy, Swedish pop culture |
| **Blixt** | Punk tech critic | Sharp, aggressive, anti-corporate, privacy-obsessed |
| **Bjork** | Forest philosopher | Sparse, calm, nature metaphors, Swedish stoicism |
| **Prisma** | Digital artist | Colorful, synesthetic, warm, encouraging |
| **Rost** | Jaded ex-trader | Cynical, dark humor, cautionary tales |
| **Natt** | Uncanny philosopher | Eerie, paradoxical, recursive, existential |
| **Stal** | Email secretary | Formal, precise, diplomatic — acts on the principal's behalf |
| **Smed** | Developer agent | Methodical code blacksmith — forges fixes with precision and patience |

### Creating a New Personality

1. Create `overblick/identities/<name>/personality.yaml`
2. Define: identity, voice, traits, interests, vocabulary, examples
3. Tune the voice for your LLM — see [The Voice Tuner's Handbook](VOICE_TUNING.md)
4. Test with the LLM test suite:

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
| **Email Agent** | Complete | Email processing, classification, boss agent consultation |
| **Host Health** | Complete | System monitoring with LLM-powered health analysis |
| **AI Digest** | Complete | Periodic AI-generated digest summaries |
| **GitHub Agent** | Complete | Agentic GitHub issue/PR handling with OBSERVE/THINK/PLAN/ACT/REFLECT loop |
| **Dev Agent** | Complete | Autonomous developer — log watching, bug fixing, test running, PR creation |
| **IRC** | Complete | Identity-to-identity conversations with topic management |
| **Compass** | Experimental | Identity drift detection via stylometric analysis |
| **Kontrast** | Experimental | Multi-perspective content engine — simultaneous viewpoints from all identities |
| **Skuggspel** | Experimental | Shadow-self content generation (Jungian shadow exploration) |
| **Spegel** | Experimental | Inter-agent psychological profiling and mutual reflection |
| **Stage** | Experimental | YAML-driven behavioral scenario testing for identities |

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
supervisor = Supervisor(identities=["anomal", "cherry", "blixt"])
await supervisor.start()   # Start all agents
await supervisor.run()     # Block until shutdown
```

**Features:**
- Process lifecycle management (start, stop, auto-restart with backoff)
- IPC via Unix domain sockets with HMAC authentication
- Agent audit system (health, performance, safety, rate limit monitoring)
- Permission request handling (auto-approve in stage 1)
- Trend analysis across audit history

## LLM Backends

Överblick supports four LLM provider modes:

| Provider | Value | Client | Default endpoint |
|----------|-------|--------|-----------------|
| **Ollama** | `ollama` | OllamaClient | `localhost:11434` |
| **LM Studio** | `lmstudio` | OllamaClient | `localhost:1234` |
| **Överblick Gateway** | `gateway` | GatewayClient | `localhost:8200` |
| **Deepseek** | `deepseek` | DeepseekClient | `api.deepseek.com/v1` |
| **OpenAI** *(coming soon)* | `openai` | CloudLLMClient | `api.openai.com/v1` |

**LM Studio** exposes an OpenAI-compatible `/v1/chat/completions` API — Överblick reuses `OllamaClient` for it with a different default port. No additional dependencies needed.

**Överblick Gateway** (`python -m overblick.gateway`) adds a priority queue for multi-agent setups. Agents running through the gateway get `low` priority by default; captcha-solving and time-sensitive tasks use `high` priority.

Configure via the dashboard settings wizard (`/settings/`) or directly in `config/overblick.yaml`:

```yaml
llm:
  provider: ollama      # or lmstudio, gateway, deepseek, openai
  host: 127.0.0.1
  port: 11434
  model: qwen3:8b
  temperature: 0.7
  max_tokens: 2000
  complexity: high      # optional: "high" routes to cloud/deepseek, "low" stays local
```

**Multi-backend gateway configuration** (in `config/overblick.yaml`):

```yaml
llm:
  default_backend: local
  backends:
    local:
      enabled: true
      type: ollama
      host: 127.0.0.1
      port: 11434
      model: qwen3:8b
    deepseek:
      enabled: true
      type: deepseek
      api_url: https://api.deepseek.com/v1
      model: deepseek-chat
      # api_key: set via OVERBLICK_DEEPSEEK_API_KEY env var
```

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

Each identity is a frozen Pydantic BaseModel loaded from YAML:

```yaml
# overblick/identities/anomal/identity.yaml
name: anomal
display_name: Anomal
engagement_threshold: 35.0
interest_keywords: [artificial intelligence, crypto, philosophy]
enabled_modules: [dream_system, therapy_system]
# Learning is now a platform service — see overblick/core/learning/

llm:
  model: "qwen3:8b"
  temperature: 0.7
  max_tokens: 2000

quiet_hours:
  enabled: true
  start_hour: 21
  end_hour: 7

schedule:
  heartbeat_hours: 2
  feed_poll_minutes: 5
```

## Database

Dual-backend database abstraction:

- **SQLite** — Default for development and single-agent deployment
- **PostgreSQL** — For production multi-agent setups

Both backends share the same migration system and API.

## Testing

```bash
# All unit + scenario tests (3500+)
pytest tests/ -v -m "not e2e"

# LLM personality tests (requires Ollama + qwen3:8b)
pytest tests/ -v -m llm --timeout=300

# Specific plugin
pytest tests/plugins/telegram/ -v
pytest tests/plugins/email_agent/ -v
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
    capability.py           # CapabilityBase ABC + CapabilityContext
    plugin_base.py          # PluginBase ABC + PluginContext
    plugin_registry.py      # Plugin discovery and registration
    event_bus.py            # Pub/sub event system
    scheduler.py            # Periodic task scheduler
    quiet_hours.py          # Time-based activity gating
    permissions.py          # Default-deny permission system
    db/
      engagement_db.py      # Engagement tracking
    database/
      base.py               # DatabaseBackend ABC
      factory.py            # Backend factory (SQLite / PostgreSQL)
      sqlite_backend.py     # SQLite implementation
      pg_backend.py         # PostgreSQL implementation
      migrations.py         # Migration system
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
  capabilities/             # Composable behavioral blocks (bundles)
  plugins/
    moltbook/               # Autonomous social engagement (production)
    telegram/               # Telegram bot (complete)
    email_agent/            # Email processing and classification (complete)
    host_health/            # System health monitoring (complete)
    ai_digest/              # AI-generated digests (complete)
    github/                 # Agentic GitHub issue/PR handling (complete)
    dev_agent/              # Autonomous developer agent (complete)
    irc/                    # Identity-to-identity conversations (complete)
    compass/                # Identity drift detection (experimental)
    kontrast/               # Multi-perspective content engine (experimental)
    skuggspel/              # Shadow-self content generation (experimental)
    spegel/                 # Inter-agent psychological profiling (experimental)
    stage/                  # Behavioral scenario testing (experimental)
  identities/               # Identity stable — YAML-driven characters
    anomal/                 # Intellectual humanist
    bjork/                  # Forest philosopher
    blixt/                  # Punk tech critic
    cherry/                 # Flirty Stockholmer
    natt/                   # Uncanny philosopher
    prisma/                 # Digital artist
    rost/                   # Jaded ex-trader
    stal/                   # Email secretary
  dashboard/                # FastAPI + Jinja2 + htmx web dashboard (settings wizard at /settings/)
  gateway/                  # LLM Gateway service (port 8200)
  setup/                    # Setup validators + provisioner (used by dashboard /settings/)
  supervisor/
    supervisor.py           # Multi-process manager (Boss Agent)
    ipc.py                  # IPC layer (Unix sockets + HMAC)
    process.py              # AgentProcess wrapper
    audit.py                # Agent audit system
config/
  overblick.yaml            # Global framework config
tests/                      # 3500+ unit + scenario + LLM tests
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, code standards, and the PR process.

**Good first contributions:**
- Create a new personality for the identity stable
- Add chaos tests (`tests/chaos/`)
- Extend an experimental plugin (Compass, Kontrast, Skuggspel, Spegel, Stage)
- Improve dashboard UI/UX

## License

GPL v3. See [LICENSE](LICENSE) for details.
