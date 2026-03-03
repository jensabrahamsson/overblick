# Överblick Security

## Overview

Överblick is a **security-first** multi-identity agent framework. Every LLM interaction passes through a 6-layer fail-closed pipeline. This document describes the threat model, security guarantees, limitations, and reporting process.

## Threat Model

Överblick is designed to protect against:

### 1. **Prompt Injection & Jailbreaks**
- Attempts to override system instructions
- Hidden commands in user input
- Multi-message attacks
- Persona hijacking

### 2. **AI Language Leakage**
- LLM revealing it's an AI/model
- Mentioning internal rules or safety guidelines
- Breaking character/persona

### 3. **Content Safety Violations**
- Harmful instructions (violence, self-harm)
- Hate speech, discrimination
- Dangerous technical instructions

### 4. **Resource Exhaustion**
- Rate limit bypass attempts
- Denial-of-service via LLM calls
- Memory exhaustion through large inputs

### 5. **Local Process Isolation**
- Unauthorized inter-process communication
- Privilege escalation between identities
- Secret leakage between identities

### 6. **Configuration & Secret Exposure**
- Hardcoded credentials in plugins
- Unencrypted secrets at rest
- Permission bypass through misconfiguration

## Security Architecture

Överblick implements **defense-in-depth** with 6 interlocking layers:

### Layer 1: Input Sanitizer
- Strips null bytes, control characters (except `\n`, `\t`, `\r`)
- Unicode NFC normalization
- Length truncation (10,000 characters)
- **Boundary markers** wrap external content: `<<<EXTERNAL_*_START>>> ... <<<EXTERNAL_*_END>>>`

### Layer 2: Preflight Checker (3 sublayers)
1. **Fast pattern matching** — 17 instant-block, 8 suspicion patterns
2. **AI analysis** — LLM-classifies suspicious messages (confidence ≥ 0.7)
3. **User context tracking** — Per-user suspicion scoring with temporary bans

### Layer 3: Rate Limiter
- Token bucket algorithm with LRU eviction
- Per-key limits (e.g., `"llm_pipeline:user123"`)
- Configurable burst capacity and refill rate

### Layer 4: LLM Call
- Actual model invocation
- **Fail-closed**: Empty response or error → blocked result

### Layer 5: Output Safety (4 sublayers)
1. **AI language detection** — Blocks "I am an AI", model names, etc.
2. **Persona break detection** — Blocks "I'm not {identity}", role-breaking statements
3. **Banned slang replacement** — Identity-specific word filtering with replacements
4. **Blocked content** — Harmful content patterns

### Layer 6: Audit Log
- Structured SQLite audit trail
- Every action logged with identity, plugin, success/failure
- Automatic 90-day retention cleanup

## Additional Protections

### IPC Security
- Unix domain sockets (macOS/Linux) or TCP localhost (Windows)
- HMAC-authenticated messages with `hmac.compare_digest`
- Message size limits (1MB)
- Rate limiting on IPC senders
- Token files with restrictive permissions (0600)

### Secrets Management
- Fernet encryption at rest (AES-128-CBC)
- Per-identity secret isolation
- Master key in macOS Keychain (or file with 0600 fallback)
- `ctx.get_secret("key")` API — never hardcode credentials

### Permission System
- **Default-deny** — actions must be explicitly permitted
- Per-identity permission rules (YAML config)
- Rate limits, cooldowns, boss-agent approval requirements
- `PermissionChecker.is_allowed("action")` runtime checks

### Plugin Isolation
- Plugins only access framework via `PluginContext`
- No direct filesystem/network access outside provided APIs
- Per-identity data directory isolation
- Capability system (planned) for fine-grained control

## What Överblick Does **NOT** Protect Against

### 1. **Malicious Plugins**
- Plugins run with the same permissions as the identity
- A malicious plugin can bypass all security layers
- **Mitigation**: Only run plugins from trusted sources, review plugin code

### 2. **Physical Access Attacks**
- Anyone with physical access to the host can read/write files
- Secrets are encrypted at rest but decrypted in memory
- **Mitigation**: Use full-disk encryption, secure host access

### 3. **Network Attacks** (when plugins expose network services)
- Plugins that open network ports are outside the security boundary
- No built-in firewall or network isolation
- **Mitigation**: Run behind firewall, use VPN for remote access

### 4. **Supply Chain Attacks**
- Compromised Python packages in dependencies
- Malicious LLM models (Ollama pulls from untrusted registries)
- **Mitigation**: Pin dependencies, verify checksums, use trusted model sources

### 5. **Social Engineering**
- Convincing an identity to reveal secrets via legitimate conversation
- Social engineering the human operator
- **Mitigation**: Operator education, secret redaction in logs

### 6. **Advanced Adversarial ML Attacks**
- Sophisticated prompt injections that evade pattern matching
- AI-generated jailbreaks that pass preflight checks
- **Mitigation**: Regular updates to preflight patterns, community testing

### 7. **Side-Channel Attacks**
- Timing attacks on HMAC comparison (mitigated by `hmac.compare_digest`)
- Memory inspection via `/proc` or debugging tools
- **Mitigation**: Secure host configuration, disable debug interfaces

## Safe by Default Configuration

Överblick now enables **strict mode by default** (since version 0.1.0-beta):

- `SafeLLMPipeline(strict=True)` requires all security components
- Missing preflight checker, output safety, or rate limiter raises `ConfigError`
- **Opt-out**: Set environment variable `OVERBLICK_SAFE_MODE=0`
- **Beta testers**: We recommend keeping strict mode enabled

**Skip flags are dangerous**: `skip_preflight=True` and `skip_output_safety=True` bypass critical security layers. Use only for:
- Internal system prompts (supervisor handlers)
- Code analysis where injection risk is minimal
- **Never** expose skip flags to untrusted input paths

**Raw LLM client protection**: Plugin access to raw LLM client (`ctx.llm_client`) is disabled by default.
- Default: `OVERBLICK_RAW_LLM=0` (raises RuntimeError when accessed)
- Migration: `OVERBLICK_RAW_LLM=1` allows raw access (not recommended for production)
- Use `ctx.llm_pipeline` for secure LLM calls with full security chain
- `ResponseGenerator` requires `llm_pipeline` or explicit `allow_raw_fallback=True`

**Strict capability enforcement**: Capability system warns by default.
- Default: `OVERBLICK_STRICT_CAPABILITIES=0` (warnings only)
- Strict: `OVERBLICK_STRICT_CAPABILITIES=1` (raises PermissionError for missing grants)
- Configure grants in identity YAML under `plugin_capabilities:` section

## Beta Testing Security Guidelines

If you're testing Överblick in external beta:

1. **Start with strict mode enabled** (`OVERBLICK_SAFE_MODE=1`, default)
2. **Review plugin permissions** before enabling new plugins
3. **Monitor audit logs** for blocked attempts and false positives
4. **Test with non-privileged identities** first (no email/SMS/API access)
5. **Use isolated test environments** — not production credentials

## Reporting Security Vulnerabilities

**Please do NOT open public GitHub issues for security vulnerabilities.**

### Responsible Disclosure Process

1. **Email**: security@overblick.ai (PGP key available on request)
2. **Include**:
   - Description of the vulnerability
   - Steps to reproduce
   - Impact assessment
   - Suggested fix (if any)

### Response Commitment

- **Acknowledgement**: Within 48 hours
- **Assessment**: Within 7 days
- **Fix timeline**: Depends on severity (critical: <72 hours, high: <1 week)
- **Disclosure**: Coordinated after fix is released

### Scope

**In-scope**:
- Security bypass in SafeLLMPipeline
- Permission escalation
- Secret leakage
- Remote code execution via plugin API

**Out-of-scope**:
- Theoretical attacks without proof-of-concept
- Social engineering
- Physical access attacks
- Issues in dependencies (report upstream)

## Security Updates

- Subscribe to GitHub Releases for security updates
- Critical security fixes will be marked with `[SECURITY]` in release notes
- Consider enabling Dependabot for dependency updates

## Further Reading

- [ARCHITECTURE.md](./ARCHITECTURE.md) — Detailed security architecture
- [overblick/core/security/README.md](./overblick/core/security/README.md) — Security module documentation
- [AGENTS.md](./AGENTS.md) — Framework overview and agent guidelines

---

*Last updated: March 2025*  
*Överblick Security Team*