# Security Module

## Overview

Security-first components implementing fail-closed principles throughout. Every external input is sanitized, every LLM output is safety-checked, every action is audited.

## Components

### Input Sanitizer (`input_sanitizer.py`)

Sanitizes all external content before LLM processing. Uses boundary markers to clearly delineate untrusted content:

```
────────── EXTERNAL CONTENT START [post_title] ──────────
[user content here]
────────── EXTERNAL CONTENT END [post_title] ──────────
```

Implements iterative stripping to defeat nested injection attempts.

### Preflight Checker (`preflight.py`)

Analyzes incoming messages for manipulation attempts before they reach the LLM:
- Jailbreak detection ("ignore instructions", "developer mode")
- Prompt injection ("system override", hidden instructions)
- Extraction attempts (reveal system prompts, internal rules)

Returns structured results with block reasons and deflection text.

### Output Safety (`output_safety.py`)

4-layer defense against LLM output leakage:
1. **AI Language Detection** — "I am an AI", "I am Claude", "my safety guidelines"
2. **Persona Break Detection** — "I'm not {identity_name}", role-breaking statements
3. **Banned Slang Filtering** — Identity-specific word filters with replacements
4. **Blocked Content** — Harmful content patterns

### Rate Limiter (`rate_limiter.py`)

Token-bucket rate limiting with per-user composite keys. Prevents abuse while allowing burst capacity.

### Audit Log (`audit_log.py`)

Structured SQLite audit trail. Non-blocking writes via `ThreadPoolExecutor` when running in async context. Automatic retention-based cleanup (default 90 days).

### Secrets Manager (`secrets_manager.py`)

Per-identity Fernet-encrypted secrets storage. Accessed via `ctx.get_secret("key")`. Personal names and credentials are secrets, not config.

## Design Principles

- **Fail-closed**: If any security component crashes, the request is blocked
- **Boundary markers**: External content is always clearly marked as untrusted
- **Per-identity isolation**: Each identity has its own secrets, audit log, and data
- **Non-blocking audit**: SQLite writes are offloaded to background threads
- **Safe by default**: Strict mode enabled by default (`OVERBLICK_SAFE_MODE=1`)

## Safe by Default Configuration

Överblick now enables **strict mode by default** (since version 0.1.0-beta):

- `SafeLLMPipeline(strict=True)` requires all security components
- Missing preflight checker, output safety, or rate limiter raises `ConfigError`
- **Opt-out**: Set environment variable `OVERBLICK_SAFE_MODE=0`
- **Raw LLM client protection**: `PluginContext.llm_client` disabled by default (requires `OVERBLICK_RAW_LLM=1`)
- **ResponseGenerator pipeline enforcement**: Requires `SafeLLMPipeline` or explicit `allow_raw_fallback=True`
