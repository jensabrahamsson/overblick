# CLAUDE.md — Överblick Agent Framework

## Quality Standard
**PERFECTION IS THE STANDARD.** Every file, every test, every prompt, every line of code must be production-grade. No shortcuts. No "good enough." No TODO comments left behind. If it's worth building, it's worth building right.

## Overview
Överblick is a security-focused multi-identity agent framework with a personality stable. It consolidates multiple agent personalities (Anomal, Cherry, Blixt, Björk, Prisma, Rost, Natt, Stål, Smed, PolyTrader, Vakt) plus a special Supervisor system identity into ONE codebase with a plugin architecture. PolyTrader is a data-driven prediction market analyst and trader. Vakt ("Guard") is a vigilant log monitoring and alerting agent. The Supervisor is the boss agent that manages and governs all plugin agents in the collective.

## Architecture
- **Core:** Orchestrator, identity system, plugin registry, event bus, scheduler, capability system, permission system
- **Security:** SafeLLMPipeline (fail-closed), preflight checks, output safety, audit log, secrets manager, input sanitizer with boundary markers, rate limiter
- **LLM:** Abstract client with two core implementations: OllamaClient (also used for LM Studio) and GatewayClient. The Gateway additionally includes a DeepseekClient for cloud API routing. SafeLLMPipeline wraps ALL LLM calls (sanitize → preflight → rate limit → LLM → output safety → audit).
- **Database:** Abstract backend (SQLite + PostgreSQL) with migration system
- **Learning:** Per-identity knowledge acquisition (`LearningStore`) with ethos-gated LLM review, embedding-based semantic retrieval, and SQLite persistence. Injected into all plugins via `PluginContext.learning_store`. Replaces the old `safe_learning` capability.
- **Agentic:** Reusable OBSERVE/THINK/PLAN/ACT/REFLECT loop (`AgenticPluginBase`, `ActionPlanner`, `ActionExecutor`, `ReflectionPipeline`, `GoalTracker`). Used by GitHub Agent and Dev Agent plugins.
- **Plugins:** Self-contained modules that receive PluginContext as their only framework interface. See classification below.
- **Identities:** Unified identity stable — YAML-driven character definitions (voice, traits, interests, backstory, psychology, key_knowledge, operational config) loadable by any plugin via `load_identity()` + `build_system_prompt()`. Each identity is a single `personality.yaml` containing both character AND operational config.
- **Supervisor:** Multi-process boss agent with IPC (authenticated Unix sockets on macOS/Linux, TCP localhost on Windows), permission management, agent audit system.
- **Dashboard:** FastAPI + Jinja2 + htmx web dashboard. Read-only agent monitoring, audit trail, identity browsing, and integrated 9-step settings wizard (at `/settings/`). On first run (no `config/overblick.yaml`), the dashboard auto-redirects to the wizard. No npm — vendored htmx, hand-crafted dark theme CSS.

### Plugin Classification

| Plugin | Type | Base Class | Purpose |
|--------|------|-----------|---------|
| **moltbook** | Content | PluginBase | Forum posting and social presence |
| **telegram** | Communication | PluginBase | Telegram bot integration |
| **email_agent** | Communication | PluginBase | Email monitoring and response |
| **irc** | Communication | PluginBase | IRC channel participation |
| **ai_digest** | Content | PluginBase | RSS feed analysis and summarization |
| **kontrast** | Content | PluginBase | Multi-perspective commentary (fan-out) |
| **skuggspel** | Content | PluginBase | Jungian shadow-self content generation |
| **spegel** | Content | PluginBase | Inter-agent psychological profiling |
| **compass** | Monitoring | PluginBase | Identity health scoring and trend tracking |
| **stage** | Testing | PluginBase | Behavioral scenario test execution |
| **host_health** | Monitoring | PluginBase | System health metrics collection |
| **github** | **Agentic** | AgenticPluginBase | Autonomous GitHub issue/PR management (OBSERVE/THINK/PLAN/ACT/REFLECT) |
| **dev_agent** | **Agentic** | AgenticPluginBase | Autonomous development task execution |
| **log_agent** | **Agentic** | AgenticPluginBase | Autonomous log analysis and anomaly detection |

**Agentic plugins** use the full reasoning loop (`AgenticPluginBase`) with goal tracking, action planning, and reflection. **Basic plugins** use `PluginBase` with a simpler setup/tick/teardown lifecycle.

## Running
```bash
# Run with specific identity
python -m overblick run anomal
python -m overblick run cherry
python -m overblick run smed

# Start LLM Gateway (required for LLM tests and production)
python -m overblick.gateway

# Start web dashboard (localhost:8080)
python -m overblick dashboard
python -m overblick dashboard --port 9090

# Chat with an identity (dev tool — Unix/macOS only)
./chat.sh cherry
./chat.sh blixt --temperature 0.9
# Cross-platform alternative:
python -m overblick chat cherry
python -m overblick chat blixt --temperature 0.9

# Run tests (fast — excludes LLM and E2E browser tests)
python -m pytest tests/ -v -m "not llm and not e2e"

# Run LLM personality tests (requires Gateway + Ollama + qwen3:8b)
python -m pytest tests/ -v -s -m llm

# Run slow LLM tests (multi-turn, forum posts)
python -m pytest tests/ -v -s -m llm_slow

# Run E2E browser tests (requires Playwright + running dashboard/wizard)
python -m pytest tests/ -v -m e2e

# Run ALL unit tests (excludes E2E)
python -m pytest tests/ -v -m "not e2e"

# Run dashboard tests only
python -m pytest tests/dashboard/ -v

# Manager — cross-platform Python CLI (works on macOS, Linux, Windows)
python -m overblick manage start anomal
python -m overblick manage stop anomal
python -m overblick manage status all
python -m overblick manage supervisor-start "anomal cherry natt stal"
python -m overblick manage supervisor-stop
python -m overblick manage supervisor-status
python -m overblick manage supervisor-logs

# Manager — Unix/macOS only (bash scripts)
./scripts/overblick_manager.sh start anomal
./scripts/overblick_manager.sh supervisor-start "anomal cherry natt stal"
```

## Dashboard Configuration — CRITICAL
The dashboard MUST be accessible from other devices on the local network. This is a **NON-NEGOTIABLE** requirement.

- `config/overblick.yaml` must have `network_access: true` and `password_hash: ""` for local development
- When `password_hash` is empty, auth is disabled and the dashboard binds to `0.0.0.0` (all interfaces)
- **NEVER** add a safety fallback that forces `127.0.0.1` when there is no password — the user explicitly wants passwordless network access on local networks
- **NEVER** re-add `password_hash` with a bcrypt hash unless the user explicitly asks for it
- The `NoAuthMiddleware` in `app.py` injects a dummy session when auth is disabled — routes depend on `request.state.session` existing
- If you modify dashboard auth, config, or binding: verify it still works on `0.0.0.0:8080` without login

## Key Principles
- **Perfection:** Every module tested, every edge case handled, every prompt tuned
- **Security-first:** Fail-closed pipeline, Fernet-encrypted secrets, boundary markers for external content, authenticated IPC, default-deny permissions
- **Plugin isolation:** Plugins only access framework through PluginContext
- **Identity-driven:** Characters are reusable building blocks in the identity stable — unified YAML with both character and operational config
- **No cross-contamination:** Each identity has isolated data/, logs/, secrets/
- **Documentation:** Every identity, capability, and plugin MUST have its own README.md explaining purpose, usage, configuration, and examples

## Confidential Local Plugins
- Local plugins in `overblick/plugins/_local/` are **CONFIDENTIAL**
- NEVER mention local plugins in README files, documentation, the website, or any public material
- NEVER list local plugin names in CLAUDE.md plugin tables or architecture docs
- Local plugin code and configuration are sensitive — treat as secrets
- The `_local/` directory and all contents MUST remain gitignored at all times
- Playwright tests for local plugins live in `tests/_local/` (also gitignored)
- If asked about plugins, only reference the public plugins listed in the Plugin Classification table

## Secrets Management
- Secrets are per-identity, stored in `config/secrets/<identity>.yaml` (Fernet-encrypted at rest)
- Accessed via `ctx.get_secret("key")` in plugins — NEVER hardcode credentials or personal names in code
- **CRITICAL:** Personal names (e.g. `principal_name`) are secrets, not config. The `{principal_name}` placeholder in personality YAML files is resolved at runtime from secrets, making personalities reusable across different principals.
- Key secrets for the email agent (Stål):
  - `principal_name` — the person Stål acts on behalf of (injected into prompts)
  - `telegram_bot_token` — Telegram Bot API token (for TelegramNotifier capability)
  - `telegram_chat_id` — target chat ID for notifications
- Capabilities load their secrets via `ctx.get_secret()` in `setup()` and degrade gracefully if secrets are missing (e.g. TelegramNotifier sets `configured=False`)

## Development Agent Team ("Team Tage Erlander")
The `.claude/agents/` directory contains a full development team of specialized Claude Code agents. Use the `/team` skill to activate them for structured development.

| Agent | Role | Specialty |
|-------|------|-----------|
| **Elisabeth Lindqvist** | Scrum Master | Agile ceremonies, impediment removal, team dynamics |
| **Alexander Lindgren** | Tech Lead | Architecture decisions, code quality, technical mentoring |
| **Sofia Andersson** | Fullstack Developer | React, Node.js, Python — implements features |
| **Marcus Eriksson** | DevOps Engineer | CI/CD, Docker, infrastructure, deployment |
| **Emma Larsson** | QA Engineer | Test strategy, E2E testing, quality gates |
| **Lisa Nyström** | Security Architect | Threat modeling, security reviews, compliance |
| **David Karlsson** | Data Engineer | Data pipelines, analytics, database optimization |
| **Anders Zorn** | UI/UX Designer | Interface design, user experience, accessibility |
| **Jessica Holm** | Business Analyst | Requirements, user stories, domain modeling |
| **Marcus Bergström** | Product Owner | Prioritization, roadmap, stakeholder management |
| **Stefan Johansson** | CVO | Vision, strategy, organizational alignment |

### How to use agents
```bash
# Invoke a specific agent
@elisabeth-lindqvist-sm "Plan the next sprint"
@alexander-lindgren-tech-lead "Review this architecture"
@emma-larsson-qa "Create test strategy for the dashboard"
@lisa-nystrom-security-architect "Audit the auth module"
```

## LLM Reasoning Policy
Qwen3 supports a `think` parameter that enables/disables internal reasoning (`<think>...</think>` tokens).

| Context | Reasoning | Why |
|---------|-----------|-----|
| **Agent writing posts** (Moltbook, forum) | **ON** (default) | Deep thinking produces better quality content |
| **Agent analyzing threads/content** | **ON** (default) | Analysis benefits from reasoning |
| **Interactive chat** (`chat.py` CLI) | **OFF** (`think: false`) | Fast responses, no user-visible delay |
| **Quick replies / reactions** | **OFF** | Speed over depth |

- `OllamaClient` and `GatewayClient` keep reasoning ON by default (Qwen3's default behavior). Think tokens are stripped from the final output.
- `chat.py` uses Ollama's native `/api/chat` with `think: false` for instant streaming.
- When adding new LLM call sites, decide: does this task benefit from deep thinking? If not, disable it.

## Code Standards
- All code, comments, logs in English
- Python 3.13+
- Type hints on all public interfaces
- Tests for every module — no exceptions
- LLM identity tests go through the LLM Gateway (port 8200), marked @pytest.mark.llm
- Slow LLM tests (multi-turn, forum posts) marked @pytest.mark.llm_slow
- Security: all external content wrapped in boundary markers via `wrap_external_content()`

## Cross-Platform Notes
- Bash scripts in `scripts/` (e.g. `overblick_manager.sh`, `chat.sh`, `quickstart.sh`) are **Unix/macOS only**.
- The Python CLI manager (`python -m overblick manage`) provides the same functionality cross-platform (macOS, Linux, Windows).
- Cross-platform helpers live in `overblick/shared/platform.py` (path handling, IPC transport selection, etc.).
- IPC uses Unix domain sockets on macOS/Linux and TCP localhost on Windows.

## Dependency Management
- **CRITICAL:** When adding new pip dependencies, ALWAYS update `requirements.txt` in the project root to keep it in sync.
- Core dependencies are defined in `pyproject.toml` under `[project.dependencies]`
- Optional dependency groups: `[project.optional-dependencies]` — gateway, dashboard, dev
- Run `pip freeze > requirements.txt` or manually add the new package after installing
