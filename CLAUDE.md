# CLAUDE.md — Överblick Agent Framework

## Quality Standard
**PERFECTION IS THE STANDARD.** Every file, every test, every prompt, every line of code must be production-grade. No shortcuts. No "good enough." No TODO comments left behind. If it's worth building, it's worth building right.

## Overview
Överblick is a security-focused multi-identity agent framework with a personality stable. It consolidates multiple agent identities (Anomal, Cherry, Volt, Birch, Prism, Rust, Nyx) into ONE codebase with a plugin architecture.

## Architecture
- **Core:** Orchestrator, identity system, plugin registry, event bus, scheduler, capability system, permission system
- **Security:** SafeLLMPipeline (fail-closed), preflight checks, output safety, audit log, secrets manager, input sanitizer with boundary markers, rate limiter
- **LLM:** Abstract client with Ollama + LLM Gateway backends. SafeLLMPipeline wraps ALL LLM calls (sanitize → preflight → rate limit → LLM → output safety → audit).
- **Database:** Abstract backend (SQLite + PostgreSQL) with migration system
- **Plugins:** Self-contained modules (Moltbook, Telegram, Gmail) that receive PluginContext as their only framework interface.
- **Personalities:** Reusable personality stable — YAML-driven character definitions (voice, traits, interests, examples) loadable by any plugin via `load_personality()` + `build_system_prompt()`.
- **Identities:** YAML-driven operational configuration per identity (thresholds, schedules, LLM settings).
- **Supervisor:** Multi-process boss agent with IPC (authenticated Unix sockets), permission management, agent audit system.
- **Dashboard:** FastAPI + Jinja2 + htmx web dashboard (localhost only). Read-only agent monitoring, audit trail, identity browsing, and 7-step onboarding wizard. No npm — vendored htmx, hand-crafted dark theme CSS.

## Running
```bash
# Run with specific identity
python -m overblick run anomal
python -m overblick run cherry

# Start web dashboard (localhost:8080)
python -m overblick dashboard
python -m overblick dashboard --port 9090

# Run tests (fast — excludes LLM tests)
./venv/bin/python3 -m pytest tests/ -v -m "not llm"

# Run LLM personality tests (requires Ollama + qwen3:8b)
./venv/bin/python3 -m pytest tests/ -v -s -m llm

# Run ALL tests
./venv/bin/python3 -m pytest tests/ -v

# Run dashboard tests only
./venv/bin/python3 -m pytest tests/dashboard/ -v

# Manager script
./scripts/overblick_manager.sh start anomal
./scripts/overblick_manager.sh stop anomal
./scripts/overblick_manager.sh status
```

## Key Principles
- **Perfection:** Every module tested, every edge case handled, every prompt tuned
- **Security-first:** Fail-closed pipeline, Fernet-encrypted secrets, boundary markers for external content, authenticated IPC, default-deny permissions
- **Plugin isolation:** Plugins only access framework through PluginContext
- **Personality-driven:** Characters are reusable building blocks in the personality stable
- **Identity-driven:** All operational differences controlled by identity.yaml
- **No cross-contamination:** Each identity has isolated data/, logs/, secrets/

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

## Code Standards
- All code, comments, logs in English
- Python 3.13+
- Type hints on all public interfaces
- Tests for every module — no exceptions
- LLM personality tests use real Ollama (marked @pytest.mark.llm)
- Security: all external content wrapped in boundary markers via `wrap_external_content()`
