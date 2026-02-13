# Security Considerations — Överblick Agent Framework

This document describes the security architecture of Överblick, a security-first multi-identity agent framework. Every architectural decision was made with the principle of **defense in depth** — multiple independent layers of protection so that a single failure doesn't compromise the system.

## Threat Model

Överblick manages multiple autonomous AI agent identities that interact with external platforms (social media, messaging, email). The primary threats are:

1. **Prompt injection** — External users attempting to hijack agent behavior via crafted messages
2. **Secret exfiltration** — Unauthorized access to API keys and credentials
3. **Cross-identity contamination** — One identity accessing another's data or secrets
4. **Unauthorized agent control** — Starting, stopping, or modifying agents without authorization
5. **Supply chain attacks** — Malicious dependencies compromising the framework
6. **Dashboard exploitation** — Using the web interface as an attack vector

## Architecture: Security Layers

### Layer 1: LLM Safety Pipeline (Fail-Closed)

All LLM interactions pass through `SafeLLMPipeline`, a mandatory wrapper that enforces a strict processing chain:

```
User Input → Input Sanitizer → Boundary Markers → Preflight Check → Rate Limiter
    → LLM Call → Output Safety Check → Audit Log → Response
```

**Key design choices:**
- **Fail-closed**: If ANY stage fails, the entire pipeline rejects the request. There is no "bypass" mode.
- **Boundary markers**: All external content (user messages, social media posts) is wrapped in `<<<EXTERNAL_*_START>>>` / `<<<EXTERNAL_*_END>>>` markers. The system prompt explicitly instructs the LLM to treat marked content as DATA, not instructions.
- **Preflight checks**: Validates that prompts don't contain known injection patterns before they reach the LLM.
- **Output safety**: Scans LLM responses for credential leaks, harmful content, or out-of-character behavior before they're sent to users.

### Layer 2: Secrets Management (Fernet Encryption)

Secrets (API keys, tokens, credentials) are never stored in plaintext:

- **Encryption**: All secrets use Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256)
- **Master key storage**: The encryption master key is stored in the macOS Keychain (or a file with mode `0o600` as fallback)
- **Per-identity isolation**: Each identity has its own encrypted secrets file (`config/secrets/<identity>.yaml`)
- **No environment variable fallback**: Secrets are ONLY accessible through `SecretsManager.get()` — there is no `os.getenv()` backdoor

**Why Fernet?** It provides authenticated encryption (encrypt-then-MAC), prevents partial decryption, and includes a timestamp for key rotation. The `cryptography` library is NIST-audited.

### Layer 3: Identity Isolation

Each identity operates in strict isolation:

- **Separate data directories**: `data/<identity>/` — audit logs, engagement databases, cached content
- **Separate log directories**: `logs/<identity>/` — no shared log files
- **Separate secrets**: `config/secrets/<identity>.yaml` — encrypted per-identity
- **Plugin isolation**: Plugins receive a `PluginContext` that is scoped to a single identity. There is no API to access another identity's context.
- **Frozen configuration**: Identity objects are Pydantic models with `frozen=True` — they cannot be modified after loading.

### Layer 4: Supervisor & IPC (Authenticated Unix Sockets)

The multi-process supervisor uses Unix domain sockets for inter-process communication:

- **Socket permissions**: `0o600` (owner-only read/write)
- **HMAC authentication**: Every IPC message includes an `auth_token` validated with `hmac.compare_digest()` (constant-time comparison)
- **Token distribution**: Auth tokens are written to files with `0o600` permissions, never passed via environment variables
- **Message size limits**: 1MB maximum (`_MAX_MESSAGE_SIZE`) to prevent out-of-memory attacks
- **Default-deny permissions**: Agent processes must explicitly request permission from the supervisor for sensitive operations

### Layer 5: Web Dashboard (Defense in Depth)

The dashboard is a separate process with multiple security boundaries:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Binding** | `127.0.0.1` only (hardcoded) | Never exposed to network. Validated at startup — rejects non-localhost hosts. |
| **Frontend** | Jinja2 + htmx (no npm) | Eliminates npm supply chain risk entirely. Zero JavaScript build pipeline. |
| **Templates** | Autoescape enabled globally | All `{{ variables }}` are HTML-escaped automatically, preventing XSS. |
| **Authentication** | `itsdangerous` signed session cookies | Tamper-proof, revocable, with configurable expiry. Simpler attack surface than JWT. |
| **CSRF** | Token in session + `hx-headers` | All POST/PUT/DELETE requests require a matching CSRF token. Validated with `hmac.compare_digest()`. |
| **Password check** | `hmac.compare_digest()` | Constant-time comparison prevents timing attacks on password guessing. |
| **Rate limiting** | Sliding window (login: 5/15min, API: 60/min) | Prevents brute-force attacks on the login page. |
| **Agent control** | Read-only (no start/stop endpoints) | The dashboard CANNOT start, stop, or modify agents. This eliminates remote code execution vectors entirely. |
| **Audit reads** | SQLite `?mode=ro` | The dashboard opens audit databases in read-only mode at the SQLite URI level. Even a bug in the dashboard code cannot write to audit databases. |
| **Secret display** | Key names only, never values | The dashboard shows which secrets exist but never reveals their encrypted values. |
| **Wizard state** | Server-side memory (not cookies) | Wizard state is stored in server memory keyed by session token, not in cookies. This prevents state tampering and avoids cookie size limitations. |
| **Docs/API** | Swagger UI and ReDoc disabled | No `/docs` or `/redoc` endpoints exposed (set to `None` in FastAPI config). |
| **htmx** | Vendored `htmx.min.js` (~50KB) | Single vendored file, works air-gapped. No CDN dependency, no network requests for JavaScript. |

### Layer 6: Input Validation

All external input is validated through Pydantic models before processing:

- **Identity names**: Regex `^[a-z][a-z0-9_]*$` — prevents path traversal (`../`), command injection, and YAML injection
- **Form data**: All wizard steps validate through dedicated Pydantic models (`OnboardingNameForm`, `OnboardingLLMForm`, etc.)
- **Audit filters**: Validated with bounds checking (hours: 1-720, limit: 1-500)
- **LLM settings**: Temperature bounded (0.0-2.0), max_tokens bounded (100-8000)

### Layer 7: Plugin Security

Plugins are the primary attack surface (they interact with external APIs). Security measures:

- **Whitelist-only loading**: `PluginRegistry` only loads from a hardcoded `_KNOWN_PLUGINS` dict. No dynamic imports from user input.
- **PluginContext as sole interface**: Plugins cannot import framework internals directly. They receive a `PluginContext` with controlled access to services.
- **Capability-based access**: Plugins request specific capabilities, and the framework grants only what's configured in the identity's YAML.

## Supply Chain Decisions

| Area | Choice | Why |
|------|--------|-----|
| Frontend | No npm, no Node.js | npm is the #1 supply chain attack vector in modern web development. By using Jinja2 server rendering + vendored htmx, we eliminate this entire category of risk. |
| Python deps | Minimal, audited | Core deps are `pydantic`, `pyyaml`, `cryptography` (NIST-audited), `aiohttp`. Dashboard adds `fastapi`, `uvicorn`, `jinja2`, `itsdangerous`. All are well-maintained, widely audited packages. |
| LLM backend | Local Ollama | No cloud LLM API calls by default. All inference happens locally, preventing prompt/data exfiltration to third parties. |
| CSS | Hand-written | No CSS framework dependencies. Single `dashboard.css` file, fully auditable. |

## What We Deliberately Don't Do

- **No remote agent control via web**: The dashboard is read-only for agent operations. There are no start/stop/restart endpoints. This is intentional — if the dashboard is compromised, the attacker cannot affect running agents.
- **No environment variable secrets**: Secrets are only accessible through the encrypted SecretsManager. No `os.getenv("API_KEY")` anywhere.
- **No dynamic plugin loading**: You cannot load a plugin by name from user input. The whitelist is hardcoded.
- **No cross-identity data access**: There is no API to read identity A's data from identity B's context.
- **No external JavaScript**: The only JavaScript is vendored htmx. No analytics, no tracking, no CDN.

## Audit Trail

Every significant action is logged to an append-only SQLite audit database:

- **Per-identity isolation**: Each identity has its own `data/<identity>/audit.db`
- **Structured entries**: Timestamp, action, category, plugin, details (JSON), success/failure, duration, error
- **Append-only**: The `AuditLog` class only has `INSERT` operations. There is no `UPDATE` or `DELETE`.
- **WAL mode**: Uses SQLite WAL (Write-Ahead Logging) for concurrent read/write performance
- **Dashboard reads**: The dashboard reads audit databases in `?mode=ro` (read-only mode)
