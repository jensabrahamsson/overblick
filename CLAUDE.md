# CLAUDE.md — Blick Agent Framework

## Overview
Blick is a security-focused multi-identity agent framework. It consolidates multiple Moltbook agent identities (Anomal, Cherry) into ONE codebase with a plugin architecture.

## Architecture
- **Core:** Orchestrator, identity system, plugin registry, event bus, scheduler
- **Security:** Preflight checks, output safety, audit log, secrets manager, input sanitizer, rate limiter
- **LLM:** Abstract client with Ollama + LLM Gateway backends. Response router inspects ALL API responses.
- **Plugins:** Self-contained modules (e.g. Moltbook) that receive PluginContext as their only framework interface.
- **Identities:** YAML-driven behavioral configuration per identity (thresholds, prompts, toggles).

## Running
```bash
# Run with specific identity
python -m blick run anomal
python -m blick run cherry

# Run tests
cd /Users/jens/kod/kmoon/blick && ./venv/bin/python3 -m pytest tests/ -v

# Manager script
./scripts/blick_manager.sh start anomal
./scripts/blick_manager.sh stop anomal
./scripts/blick_manager.sh status
```

## Key Principles
- **Security-first:** Fernet-encrypted secrets, structured audit logging, input sanitization
- **Plugin isolation:** Plugins only access framework through PluginContext
- **Identity-driven:** All behavioral differences controlled by identity.yaml
- **No cross-contamination:** Each identity has isolated data/, logs/, secrets/

## Code Standards
- All code, comments, logs in English
- Python 3.13+
- Type hints on all public interfaces
- Tests for every module
