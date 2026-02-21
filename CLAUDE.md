# CLAUDE.md — Överblick Agent Framework

## Quality Standard
**PERFECTION IS THE STANDARD.** Every file, every test, every prompt, every line of code must be production-grade. No shortcuts. No "good enough." No TODO comments left behind. If it's worth building, it's worth building right.

## Overview
Överblick is a security-focused multi-identity agent framework with a personality stable. It consolidates multiple agent personalities (Anomal, Cherry, Blixt, Björk, Prisma, Rost, Natt, Stål) into ONE codebase with a plugin architecture.

## Architecture
- **Core:** Orchestrator, identity system, plugin registry, event bus, scheduler, capability system, permission system
- **Security:** SafeLLMPipeline (fail-closed), preflight checks, output safety, audit log, secrets manager, input sanitizer with boundary markers, rate limiter
- **LLM:** Abstract client with Ollama, LLM Gateway, and Cloud provider backends. Cloud LLM stub supports future integration with OpenAI, Anthropic, etc. SafeLLMPipeline wraps ALL LLM calls (sanitize → preflight → rate limit → LLM → output safety → audit).
- **Database:** Abstract backend (SQLite + PostgreSQL) with migration system
- **Plugins:** Self-contained modules (Moltbook, Telegram, Email Agent, Host Health, AI Digest) that receive PluginContext as their only framework interface.
- **Identities:** Unified identity stable — YAML-driven character definitions (voice, traits, interests, backstory, psychology, key_knowledge, operational config) loadable by any plugin via `load_identity()` + `build_system_prompt()`. Each identity is a single `personality.yaml` containing both character AND operational config.
- **Supervisor:** Multi-process boss agent with IPC (authenticated Unix sockets), permission management, agent audit system.
- **Dashboard:** FastAPI + Jinja2 + htmx web dashboard (localhost only). Read-only agent monitoring, audit trail, identity browsing, and integrated 8-step settings wizard (at `/settings/`). On first run (no `config/overblick.yaml`), the dashboard auto-redirects to the wizard. No npm — vendored htmx, hand-crafted dark theme CSS.

## Running
```bash
# Run with specific identity
python -m overblick run anomal
python -m overblick run cherry

# Start LLM Gateway (required for LLM tests and production)
python -m overblick.gateway

# Start web dashboard (localhost:8080)
python -m overblick dashboard
python -m overblick dashboard --port 9090

# Chat with an identity (dev tool)
./chat.sh cherry
./chat.sh blixt --temperature 0.9

# Run tests (fast — excludes LLM and E2E browser tests)
./venv/bin/python3 -m pytest tests/ -v -m "not llm and not e2e"

# Run LLM personality tests (requires Gateway + Ollama + qwen3:8b)
./venv/bin/python3 -m pytest tests/ -v -s -m llm

# Run slow LLM tests (multi-turn, forum posts)
./venv/bin/python3 -m pytest tests/ -v -s -m llm_slow

# Run E2E browser tests (requires Playwright + running dashboard/wizard)
./venv/bin/python3 -m pytest tests/ -v -m e2e

# Run ALL unit tests (excludes E2E)
./venv/bin/python3 -m pytest tests/ -v -m "not e2e"

# Run dashboard tests only
./venv/bin/python3 -m pytest tests/dashboard/ -v

# Manager script — individual agents
./scripts/overblick_manager.sh start anomal
./scripts/overblick_manager.sh stop anomal
./scripts/overblick_manager.sh status all

# Manager script — supervisor (starts/stops ALL agents at once)
./scripts/overblick_manager.sh supervisor-start "anomal cherry natt stal"
./scripts/overblick_manager.sh supervisor-stop
./scripts/overblick_manager.sh supervisor-status
./scripts/overblick_manager.sh supervisor-logs
```

## Key Principles
- **Perfection:** Every module tested, every edge case handled, every prompt tuned
- **Security-first:** Fail-closed pipeline, Fernet-encrypted secrets, boundary markers for external content, authenticated IPC, default-deny permissions
- **Plugin isolation:** Plugins only access framework through PluginContext
- **Identity-driven:** Characters are reusable building blocks in the identity stable — unified YAML with both character and operational config
- **No cross-contamination:** Each identity has isolated data/, logs/, secrets/
- **Documentation:** Every identity, capability, and plugin MUST have its own README.md explaining purpose, usage, configuration, and examples

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
The `.claude/agents/` directory contains a full development team of specialized Claude Code agents imported from the solana-alpha-bot project. Use the `/team` skill to activate them for structured development.

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

## Dependency Management
- **CRITICAL:** When adding new pip dependencies, ALWAYS update `requirements.txt` in the project root to keep it in sync.
- Core dependencies are defined in `pyproject.toml` under `[project.dependencies]`
- Optional dependency groups: `[project.optional-dependencies]` — gateway, dashboard, dev
- Run `pip freeze > requirements.txt` or manually add the new package after installing
