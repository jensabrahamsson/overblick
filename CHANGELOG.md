# Changelog

All notable changes to Överblick will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - Beta Preparation

### Added
- **Safe by default mode**: `SafeLLMPipeline` now defaults to `strict=True` (requires all security components)
  - Environment variable `OVERBLICK_SAFE_MODE=0` to opt-out
  - Supervisor handlers explicitly use `strict=False` for internal trusted content
  - Main agent pipelines use `strict=True` for maximum security
- **Security documentation**: `SECURITY.md` with:
  - Threat model and security guarantees
  - What Överblick does NOT protect against  
  - Responsible disclosure process for vulnerabilities
  - Beta testing security guidelines
- **Plugin capability system**: Minimal permission system for plugin resource access
  - Plugins declare `REQUIRED_CAPABILITIES` class variable
  - Warnings logged when capabilities missing from identity YAML
  - Standard capabilities: `network_outbound`, `filesystem_write`, `secrets_access`, etc.
  - Configuration: `plugin_capabilities:` section in identity YAML

### Changed
- **Breaking**: `SafeLLMPipeline(strict=False)` now requires explicit `strict=False` parameter
  - Tests using incomplete pipelines must set `OVERBLICK_SAFE_MODE=0` or add `strict=False`
  - Supervisor handlers updated with `strict=False`
- **Security**: Skip flags (`skip_preflight`, `skip_output_safety`) now more dangerous
  - Documented as "internal use only" in SECURITY.md
  - Should never be exposed to untrusted input paths

### Fixed
- **LSP type errors**: Multiple generator return type fixes in test fixtures
- **SQLite thread safety**: Thread-local connections in `inet_auth.py`
- **Client IP validation**: Added `trusted_proxies` config and secure IP extraction
- **Persistent bans**: Violation tracker now uses SQLite for ban persistence

### Security
- **Fail-closed enforcement**: Pipeline crashes block requests (not pass through)
- **Input validation**: Client IP header validation with trusted proxy CIDR ranges
- **Boundary markers**: External content wrapped with injection-resistant markers
- **Audit logging**: All security decisions logged with structured JSON

## [0.0.1] - 2025-02-12

### Initial Release
- Multi-identity agent framework with personality stable
- 6-layer SafeLLMPipeline: sanitize → preflight → rate limit → LLM → output safety → audit
- Plugin architecture with 15+ plugins (Moltbook, Telegram, Email Agent, GitHub, etc.)
- Supervisor (boss agent) with IPC security (HMAC auth)
- Fernet-encrypted secrets with macOS Keychain/Linux keyring support
- Dashboard with settings wizard (FastAPI + htmx)
- LLM Gateway with priority queue and multi-backend routing
- 3500+ unit and scenario tests

---

## Upgrade Notes

### From pre-0.0.1 to Beta

1. **Safe mode enabled by default**
   - If you have custom pipelines without all security components, set `OVERBLICK_SAFE_MODE=0`
   - Or explicitly pass `strict=False` to `SafeLLMPipeline()`

2. **Plugin capability warnings**
   - Check logs for warnings about missing capability grants
   - Add `plugin_capabilities:` section to identity YAML if needed
   - Example:
     ```yaml
     plugin_capabilities:
       telegram:
         network_outbound: true
         secrets_access: true
     ```

3. **Security documentation**
   - Review `SECURITY.md` for threat model and reporting process
   - Understand what Överblick does NOT protect against (malicious plugins, etc.)

## Reporting Issues

- **Security vulnerabilities**: Email security@overblick.ai (do NOT open public issues)
- **Bugs**: GitHub Issues with reproduction steps
- **Feature requests**: GitHub Discussions

---

*Maintained by the Överblick Security Team*