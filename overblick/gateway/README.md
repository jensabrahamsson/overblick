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
      host: "10.8.0.24"
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
