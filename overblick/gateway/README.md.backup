# LLM Gateway

Multi-backend priority queue server that routes LLM requests from multiple agents through shared GPU resources. Protects against concurrent access, provides intelligent routing based on task complexity, and ensures fair scheduling across priorities.

## Architecture

```
Agent (Cherry)  ──┐
Agent (Anomal)  ──┤                    ┌─ local   (Ollama, qwen3:8b)
Agent (Natt)    ──┼─→ Gateway ─→ Router ─┼─ cloud   (LM Studio, devstral-2-123b-iq5)
Agent (Stal)    ──┤    ↓               └─ deepseek (Deepseek API, deepseek-chat / deepseek-reasoner)
Challenge solver ─┘  Priority Queue
                     (HIGH before LOW)
```

**Single worker** — only one request hits the GPU at a time. Requests queue up ordered by priority, then FIFO within the same priority level.

## Quick Start

```bash
# Start the gateway
python -m overblick.gateway

# Health check
curl http://localhost:8200/health

# Or via the manager script
./scripts/overblick_manager.sh gateway start
./scripts/overblick_manager.sh gateway status
```

Default port: **8200** (override with `OVERBLICK_GW_API_PORT`).

## API

### POST /v1/chat/completions

OpenAI-compatible chat completions endpoint.

```bash
curl -X POST "http://localhost:8200/v1/chat/completions?priority=high&complexity=ultra" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is 2+2?"}
    ],
    "max_tokens": 200,
    "temperature": 0.7,
    "top_p": 0.9
  }'
```

**Query parameters:**

| Parameter | Values | Default | Purpose |
|-----------|--------|---------|---------|
| `priority` | `high`, `low` | `low` | Queue ordering — HIGH jumps ahead of LOW |
| `complexity` | `einstein`, `ultra`, `high`, `low` | none | Backend selection — which model is capable enough |
| `backend` | `local`, `cloud`, `deepseek` | none | Explicit backend override (bypasses routing) |

**Request body fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `qwen3:8b` | Model name |
| `messages` | array | required | Chat messages (`role` + `content`) |
| `max_tokens` | int | 2000 | Max tokens to generate (1–8192) |
| `temperature` | float | 0.7 | Sampling temperature (0.0–2.0) |
| `top_p` | float | 0.9 | Nucleus sampling threshold (0.0–1.0) |

**Response** — standard OpenAI chat completion format:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "qwen3:8b",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "4"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 25, "completion_tokens": 1, "total_tokens": 26}
}
```

### POST /v1/embeddings

Generate text embeddings via the default backend's Ollama `/api/embed` endpoint.

```bash
curl -X POST "http://localhost:8200/v1/embeddings?text=Hello+world&model=nomic-embed-text"
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `text` | required | Text to embed |
| `model` | `nomic-embed-text` | Embedding model name |

**Response:**

```json
{"embedding": [0.123, -0.456, ...], "model": "nomic-embed-text"}
```

### GET /health

```json
{
  "status": "healthy",
  "gateway": "running",
  "backends": {
    "local": {"status": "connected", "type": "ollama", "model": "qwen3:8b", "default": true},
    "deepseek": {"status": "cloud_configured", "type": "deepseek", "model": "deepseek-chat", "default": false}
  },
  "default_backend": "local",
  "queue_size": 0,
  "max_queue_size": 100,
  "gpu_starvation_risk": "low",
  "avg_response_time_ms": 125.5,
  "active_requests": 0
}
```

- `status`: `healthy` (at least one backend up) or `degraded` (no backends)
- `gpu_starvation_risk`: `low` (<3 queued), `medium` (3-7), `high` (8+)
- `backends`: Per-backend object with `type`, `model`, `default`, and `status` (`connected` / `cloud_configured` / `disconnected`)

### GET /stats

Detailed queue statistics: request counts (total, high, low), average response time, uptime.

### GET /backends

List all configured backends with connection status.

### GET /models?backend=local

List available models from a specific backend.

## Routing

The router selects which backend handles each request. **7-step precedence:**

1. **Explicit `backend=` parameter** — highest priority, bypasses all logic. Returns HTTP 400 if backend does not exist.
2. **`complexity=einstein`** — deepseek only (uses `deepseek-reasoner` model). No fallback to other providers — reasoning is DeepSeek-specific.
3. **`complexity=ultra`** — prefers `deepseek` > `cloud` > `local` (precision tasks)
4. **`complexity=high`** — prefers `cloud` > `deepseek` > `local` (offload heavy work)
5. **`complexity=low`** — prefers `local` (save cloud costs)
6. **Priority-based routing** (legacy, if no complexity): `priority=high` + cloud available → uses `cloud`
7. **Default fallback** → `default_backend` from configuration

For complexity-based routing, if the preferred backend is unavailable the router falls through to the default.

### Priority vs Complexity

These are orthogonal concepts:

- **Priority** = how urgent (queue ordering). HIGH jumps ahead of LOW in the queue.
- **Complexity** = how capable the backend needs to be (routing). EINSTEIN/ULTRA routes to the most powerful model.

A request can be `priority=low, complexity=ultra` (background task that needs a powerful model) or `priority=high, complexity=low` (interactive request that a small model can handle).

## Einstein Mode

Einstein mode activates DeepSeek's reasoning model for deep analysis tasks.

```bash
curl -X POST "http://localhost:8200/v1/chat/completions?priority=high&complexity=einstein" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Analyze this architecture..."}]}'
```

**How it works:**

1. Router resolves `complexity=einstein` → deepseek backend only
2. Gateway overrides model to `deepseek-reasoner` regardless of request model
3. DeepSeek API returns `reasoning_content` (thinking process) alongside `content` (final answer)
4. Both fields are preserved in the response `choices[0].message`

**Response with reasoning:**

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "The final answer...",
      "reasoning_content": "Let me think step by step..."
    }
  }]
}
```

**No fallback**: If deepseek is unavailable, einstein requests fall back to default backend but WITHOUT reasoning capabilities. The response will contain normal content only.

## Security

### Origin Check

The gateway rejects any request with a non-localhost `Origin` header. This prevents CSRF attacks from remote websites hitting the gateway.

Allowed origins: `http://127.0.0.1`, `http://localhost`, `https://127.0.0.1`, `https://localhost` (any port).

### API Key Authentication

Optional API key protection via the `X-API-Key` header:

```bash
# Set a key (either env var works)
export OVERBLICK_GW_API_KEY=my-secret-key
# or
export OVERBLICK_GATEWAY_KEY=my-secret-key

# Authenticated request
curl -H "X-API-Key: my-secret-key" http://localhost:8200/stats
```

If no key is configured, all requests are allowed (localhost-only deployment assumed). The `/health` endpoint is unauthenticated. All other endpoints require the key when configured.

### Deepseek API Key Safety

Deepseek API keys should **never** be placed directly in `config/overblick.yaml` — they will be committed to git. Use either:

- Environment variable: `export OVERBLICK_DEEPSEEK_API_KEY=sk-xxx`
- Per-identity encrypted secrets: `config/secrets/<identity>.yaml`

## Connection Error Fallback

When a backend fails with a connection error, the gateway automatically retries with an alternative backend:

1. Request sent to resolved backend (e.g. `deepseek`)
2. Backend returns `ConnectionError` or `TimeoutError`
3. Router re-resolves with the failed backend excluded
4. If an alternative is found, request is retried once
5. If retry also fails, original error is propagated

This provides graceful degradation — if the cloud backend is temporarily unreachable, requests fall back to local inference.

## Reasoning & Think Tokens

The gateway handles two distinct reasoning mechanisms:

### Qwen3 Think Tokens

Qwen3 produces `<think>...</think>` blocks during reasoning. These are:

- **Enabled by default** in the gateway (no `think: false` override)
- **Stripped** by `GatewayClient` before returning to plugins (via `strip_think_tokens()`)
- Useful for agent content writing and analysis tasks

### DeepSeek Reasoning Content

DeepSeek Reasoner (`complexity=einstein`) returns a separate `reasoning_content` field:

- **Not embedded in content** — it's a separate field in the API response
- **Preserved through the gateway** — returned as `choices[0].message.reasoning_content`
- `GatewayClient` extracts it into the result dict as `result["reasoning_content"]`

## Configuration

Gateway configuration lives in `config/overblick.yaml` under the `llm` key:

```yaml
llm:
  gateway_url: "http://127.0.0.1:8200"
  default_backend: "local"
  temperature: 0.7
  max_tokens: 2000

  backends:
    local:
      enabled: true
      type: "ollama"
      host: "127.0.0.1"
      port: 11434
      model: "qwen3:8b"

    cloud:
      enabled: true
      type: "lmstudio"
      host: "<your-lmstudio-host>"
      port: 1234
      model: "devstral-2-123b-iq5"

    deepseek:
      enabled: false
      type: "deepseek"
      api_url: "https://api.deepseek.com/v1"
      api_key: "sk-xxx"
      model: "deepseek-chat"
```

### Backend Types

| Type | Client | Auth | Use Case |
|------|--------|------|----------|
| `ollama` | OllamaClient | None | Local Ollama instance |
| `lmstudio` | OllamaClient | None | LM Studio (OpenAI-compatible API) |
| `deepseek` | DeepseekClient | Bearer token | Deepseek cloud API (`deepseek-chat` + `deepseek-reasoner`) |

### Environment Variable Overrides

All overridable with `OVERBLICK_GW_*` prefix:

| Variable | Purpose | Default |
|----------|---------|---------|
| `OVERBLICK_GW_API_PORT` | Gateway listen port | 8200 |
| `OVERBLICK_GW_API_HOST` | Gateway listen address | `127.0.0.1` |
| `OVERBLICK_GW_OLLAMA_HOST` | Ollama host | 127.0.0.1 |
| `OVERBLICK_GW_OLLAMA_PORT` | Ollama port | 11434 |
| `OVERBLICK_GW_DEFAULT_MODEL` | Default model | qwen3:8b |
| `OVERBLICK_GW_MAX_QUEUE_SIZE` | Max queued requests | 100 |
| `OVERBLICK_GW_REQUEST_TIMEOUT` | Per-request timeout (seconds) | 300 |
| `OVERBLICK_GW_MAX_CONCURRENT` | Max concurrent GPU requests | 1 |
| `OVERBLICK_GW_LOG_LEVEL` | Log verbosity | INFO |
| `OVERBLICK_GW_API_KEY` | API key for `X-API-Key` auth | — |
| `OVERBLICK_GATEWAY_KEY` | Alternative API key env var | — |
| `OVERBLICK_DEEPSEEK_API_KEY` | Auto-inject Deepseek backend | — |

## Client Integration

Agents connect via `GatewayClient` from `overblick.core.llm.gateway_client`:

```python
from overblick.core.llm.gateway_client import GatewayClient

client = GatewayClient(
    base_url="http://127.0.0.1:8200",
    model="qwen3:8b",
    default_priority="low",
    max_tokens=2000,
    temperature=0.7,
    top_p=0.9,
)

# Standard request
response = await client.chat(
    messages=[{"role": "user", "content": "Hello"}],
    priority="high",
    complexity="ultra",
)
# response = {"content": "...", "model": "qwen3:8b", "tokens_used": 200, ...}

# Einstein mode (deep reasoning)
response = await client.chat(
    messages=[{"role": "user", "content": "Analyze this complex problem..."}],
    complexity="einstein",
)
# response = {"content": "...", "reasoning_content": "Let me think...", ...}

# Embeddings
vector = await client.embed("Hello world")
# vector = [0.123, -0.456, ...]

await client.close()
```

Identity YAML configures the client automatically:

```yaml
llm:
  provider: "gateway"
  model: "qwen3:8b"
  gateway_url: "http://127.0.0.1:8200"
```

## GPU Starvation Protection

The gateway prevents GPU starvation through:

1. **Priority queue** — HIGH priority (interactive) requests bypass queued LOW priority (background) work
2. **Single worker** — `max_concurrent_requests=1` serializes GPU access
3. **Per-request timeout** — prevents hung requests from blocking the queue (default 300s)
4. **Health monitoring** — `/health` reports starvation risk based on queue depth
5. **Statistics** — `/stats` tracks HIGH vs LOW breakdown for monitoring fairness

## File Structure

| File | Purpose |
|------|---------|
| `__main__.py` | Entry point (`python -m overblick.gateway`) |
| `app.py` | FastAPI application, endpoints, lifespan management |
| `config.py` | Configuration loading (YAML + env vars), singleton |
| `models.py` | Pydantic models: Priority, Complexity, ChatRequest, ChatResponse |
| `router.py` | Request routing logic (complexity/priority to backend) |
| `queue_manager.py` | Priority queue, worker loop, statistics |
| `backend_registry.py` | Backend lifecycle management, client creation |
| `ollama_client.py` | Ollama/LM Studio HTTP client (httpx) |
| `deepseek_client.py` | Deepseek API client (httpx + Bearer auth) |

## Testing

```bash
# Gateway unit tests
./venv/bin/python3 -m pytest tests/gateway/ -v

# Integration tests
./venv/bin/python3 -m pytest tests/integration/test_gateway_integration.py -v
```

---

## Internet Gateway (Experimental — Not Yet Integrated)

### What Is This?

The Internet Gateway is a hardened reverse proxy that sits in front of the internal LLM Gateway and exposes it to the internet over TLS with API key authentication. It lives in the `inet_*.py` and `internet_gateway.py` files in this directory.

**This is not yet officially supported by Överblick.** The code is here, it works, and the tests pass — but it is not wired into the main `overblick start` workflow, the dashboard does not manage it, and the supervisor does not monitor it. If you need this and are comfortable running it yourself, go ahead. Even better: open a PR to integrate it properly.

### Why Does This Exist?

The internal LLM Gateway binds to `127.0.0.1:8200` by design. It trusts every request because it only accepts localhost connections. This is fine when your agents and your Ollama instance live on the same machine.

But what if they don't?

Common scenarios:

- **Your GPU server is at home**, and you want to use your models from a laptop on a different network, from your phone, or from a VPS running agents.
- **You have a beefy workstation at the office** running Ollama with large models, and you want your remote agents to use it.
- **You run multiple machines** and want them all to share one Ollama instance without exposing an unauthenticated LLM endpoint to the internet.

The naive solution — binding the internal gateway to `0.0.0.0` — would expose an unauthenticated, unencrypted LLM endpoint to anyone who finds the port. That is not acceptable.

The Internet Gateway solves this by placing a security perimeter between the internet and your internal gateway:

```
Internet (laptop, phone, VPS, ...)
    │
    │ HTTPS (TLS 1.2+)
    ▼
┌──────────────────────────────────────┐
│  Internet Gateway (0.0.0.0:8201)     │
│  ├─ TLS termination                 │
│  ├─ API key auth (Bearer token)     │
│  ├─ Per-key rate limiting           │
│  ├─ IP allowlist (optional)         │
│  ├─ Auto-ban on abuse               │
│  ├─ Request validation & size cap   │
│  ├─ max_tokens clamping             │
│  ├─ Full audit trail (SQLite)       │
│  └─ Error masking                   │
└──────────────────────────────────────┘
    │
    │ HTTP (127.0.0.1 only)
    ▼
┌──────────────────────────────────────┐
│  Internal Gateway (127.0.0.1:8200)   │
│  (unchanged — origin validation)     │
└──────────────────────────────────────┘
    │
    ▼
  Ollama / DeepSeek / Cloud backends
```

The internal gateway remains localhost-only. Even if an attacker discovers port 8200, the origin validation middleware rejects non-localhost requests. Defense in depth.

### How It Works

**Authentication.** Every request (except `/health`) must include a `Bearer` token in the `Authorization` header. Tokens are bcrypt-hashed API keys stored in a local SQLite database. Each key has a name (e.g. "my-laptop"), optional expiry, per-key rate limits, and scoped permissions (allowed models, allowed backends, max_tokens cap). Keys use the format `sk-ob-<32 hex chars>` — easy to identify and grep for in logs.

**Proxy flow for `/v1/chat/completions`:**

1. Middleware chain runs: request size check → IP ban check → IP allowlist → global rate limit.
2. Bearer token is extracted and verified against the key database (bcrypt, timing-safe).
3. Per-key rate limit is checked.
4. Request body is parsed with strict Pydantic validation (`extra="forbid"` — unknown fields are rejected).
5. Permission check: is this model allowed for this key? This backend?
6. `max_tokens` is clamped to `min(requested, key cap, global cap)`.
7. Request is forwarded to the internal gateway via httpx. Auth headers are stripped; the internal API key is injected if configured.
8. On success: response is passed through, token usage is extracted for audit.
9. On internal error: a generic 502 or 504 is returned. **Internal details (URLs, stack traces, backend names) are never leaked.**
10. Usage stats are updated on the key record.
11. An audit entry is written (async, non-blocking).

**Rate limiting.** Two layers: a global token bucket (default 60 RPM) applied to all requests via middleware, and a per-key token bucket (default 30 RPM, configurable per key) applied after authentication. Both use the framework's existing `RateLimiter` (token bucket with LRU eviction).

**Auto-ban.** Failed authentication attempts, rate limit violations, and other abuses are tracked per IP in a sliding window. After a configurable number of violations (default 10 in 5 minutes), the IP is banned for 1 hour. Bans are stored in memory for fast O(1) lookups and backed by the audit log for persistence.

**TLS.** Three modes: (1) provide your own cert and key (e.g. Let's Encrypt), (2) auto-generate a self-signed certificate on first start (stored in `data/internet_gateway/tls/`, valid 365 days, regenerated when expired), (3) no TLS — only allowed when binding to `127.0.0.1` (dev mode). **The gateway refuses to start without TLS on a public interface.** This is a hard safety guard, not a warning.

**Error masking.** No OpenAPI docs are exposed (`/docs`, `/redoc`, `/openapi.json` all return 404). All error responses follow OpenAI's error format (`{"error": {"message": "...", "type": "...", "code": "..."}}`). Internal errors from the upstream gateway are replaced with generic messages. An attacker learns nothing about the internal architecture from error responses.

**Audit trail.** Every request — successful or not — is logged to `data/internet_gateway/audit.db` (SQLite WAL mode). The audit records: timestamp, key ID/name, source IP, HTTP method, path, model, status code, token usage, latency, errors, and security violations. Background cleanup removes entries older than 90 days.

### Endpoints

| Method | Path | Auth | Proxied to |
|--------|------|------|------------|
| GET | `/health` | No | Local status only |
| POST | `/v1/chat/completions` | Yes | Internal gateway `/v1/chat/completions` |
| POST | `/v1/embeddings` | Yes | Internal gateway `/v1/embeddings` |
| GET | `/v1/models` | Yes | Internal gateway `/models` |

Everything else returns 404.

### Usage (Manual)

```bash
# 1. Create an API key
python -m overblick api-keys create --name "my-laptop" --expires 90d
#   → prints the full key ONCE (sk-ob-...). Store it securely.

# 2. Start the Internet Gateway
python -m overblick internet-gateway
#   → listens on 0.0.0.0:8201 with auto self-signed TLS

# 3. Test from a remote machine
curl -k https://your-server:8201/health

curl -k -H "Authorization: Bearer sk-ob-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hello"}]}' \
  https://your-server:8201/v1/chat/completions

# Dev mode (localhost only, no TLS)
python -m overblick internet-gateway --no-tls
```

**Key management:**

```bash
python -m overblick api-keys list
python -m overblick api-keys create --name "phone" --rpm 10 --models "qwen3:8b"
python -m overblick api-keys revoke <key-id>
python -m overblick api-keys rotate <key-id>
```

### Configuration

Environment variables (`OVERBLICK_INET_*` prefix) or `internet_gateway:` section in `config/overblick.yaml`:

```yaml
internet_gateway:
  port: 8201
  tls_cert_path: "/etc/letsencrypt/live/myhost/fullchain.pem"
  tls_key_path: "/etc/letsencrypt/live/myhost/privkey.pem"
  ip_allowlist: []           # CIDR notation, empty = all allowed
  global_rpm: 60
  per_key_rpm: 30
  max_tokens_cap: 4096
  max_request_bytes: 65536   # 64KB body limit
  request_timeout: 120.0     # seconds
  auto_ban_threshold: 10     # violations before ban
  auto_ban_duration: 3600    # ban duration in seconds
```

| Env Variable | Purpose | Default |
|---|---|---|
| `OVERBLICK_INET_HOST` | Bind address | `0.0.0.0` |
| `OVERBLICK_INET_PORT` | Listen port | `8201` |
| `OVERBLICK_INET_TLS_CERT_PATH` | TLS certificate path | — |
| `OVERBLICK_INET_TLS_KEY_PATH` | TLS private key path | — |
| `OVERBLICK_INET_TLS_AUTO_SELFSIGNED` | Auto-generate self-signed cert | `true` |
| `OVERBLICK_INET_INTERNAL_GATEWAY_URL` | Internal gateway URL | `http://127.0.0.1:8200` |
| `OVERBLICK_INET_INTERNAL_API_KEY` | API key for internal gateway | — |
| `OVERBLICK_INET_GLOBAL_RPM` | Global requests per minute | `60` |
| `OVERBLICK_INET_PER_KEY_RPM` | Default per-key RPM | `30` |
| `OVERBLICK_INET_MAX_TOKENS_CAP` | Global max_tokens clamp | `4096` |
| `OVERBLICK_INET_IP_ALLOWLIST` | Comma-separated CIDRs | — |
| `OVERBLICK_INET_AUTO_BAN_THRESHOLD` | Violations before ban | `10` |
| `OVERBLICK_INET_AUTO_BAN_DURATION` | Ban duration (seconds) | `3600` |

### Security Properties

| Threat | Mitigation |
|--------|-----------|
| Brute force API key | bcrypt (~100ms/attempt), auto-ban after threshold, rate limiting |
| Stolen API key | Key expiry, rotation, per-key scope, revocation, IP logged in audit |
| DDoS | Global + per-key rate limits, max_tokens clamp, request size limit |
| Prompt injection | Proxied as-is — the internal gateway's SafeLLMPipeline handles sanitization |
| SSRF via proxy | Only forwards to the hardcoded `internal_gateway_url` |
| TLS downgrade | Hard refusal: no plaintext on `0.0.0.0` |
| Information leakage | Error masking, no OpenAPI docs, generic error messages |
| Port 8200 found by attacker | Internal gateway's origin validation rejects non-localhost |
| Key enumeration | Timing-safe bcrypt (always runs at least one compare, even for unknown prefixes) |

### Internet Gateway File Structure

| File | Purpose |
|------|---------|
| `inet_models.py` | Pydantic models: APIKeyRecord, BanRecord, InetAuditEntry |
| `inet_config.py` | Configuration (env vars, YAML, safety guard) |
| `inet_auth.py` | API key CRUD (SQLite + bcrypt) |
| `inet_audit.py` | Audit trail (SQLite WAL, async writes, 90-day retention) |
| `inet_tls.py` | TLS certificate loading and self-signed generation |
| `inet_middleware.py` | Middleware stack: size limit, IP ban, allowlist, global rate limit |
| `internet_gateway.py` | FastAPI reverse proxy application |

### What's Missing (Contributions Welcome)

This is where you come in. The Internet Gateway works standalone, but it is not yet integrated into the Överblick workflow. A proper integration would involve:

- **Dashboard integration** — show Internet Gateway status, connected clients, audit trail, and key management in the web dashboard.
- **Supervisor monitoring** — have the supervisor start/stop/restart the Internet Gateway alongside agents.
- **`overblick start` integration** — optionally start the Internet Gateway as part of the standard startup sequence.
- **Streaming support** — the proxy currently buffers full responses. Streaming (SSE) for chat completions would reduce time-to-first-token for remote clients.
- **WebSocket support** — for real-time chat interfaces on remote clients.
- **Mutual TLS (mTLS)** — for environments where IP allowlisting is insufficient and you want certificate-based client authentication.
- **Key management in dashboard** — create/revoke/rotate keys from the web UI instead of the CLI.
- **Usage dashboards** — visualize per-key token usage, request patterns, and cost estimates over time.

If any of this sounds interesting, open a PR. The foundation is solid and tested.
