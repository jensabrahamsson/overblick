# LLM Gateway

Multi-backend priority queue server that routes LLM requests from multiple agents through shared GPU resources. Protects against concurrent access, provides intelligent routing based on task complexity, and ensures fair scheduling across priorities.

## Architecture

```
Agent (Cherry)  ──┐
Agent (Anomal)  ──┤                    ┌─ local   (Ollama, qwen3:8b)
Agent (Natt)    ──┼─→ Gateway ─→ Router ─┼─ cloud   (LM Studio, devstral-2-123b-iq5)
Agent (Stal)    ──┤    ↓               └─ deepseek (Deepseek API)
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
    "temperature": 0.7
  }'
```

**Query parameters:**

| Parameter | Values | Default | Purpose |
|-----------|--------|---------|---------|
| `priority` | `high`, `low` | `low` | Queue ordering — HIGH jumps ahead of LOW |
| `complexity` | `ultra`, `high`, `low` | none | Backend selection — which model is capable enough |
| `backend` | `local`, `cloud`, `deepseek` | none | Explicit backend override (bypasses routing) |

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

### GET /health

```json
{
  "status": "healthy",
  "gateway": "running",
  "backends": {"local": "connected", "cloud": "connected"},
  "default_backend": "local",
  "queue_size": 0,
  "gpu_starvation_risk": "low",
  "avg_response_time_ms": 125.5,
  "active_requests": 0
}
```

- `status`: `healthy` (at least one backend up) or `degraded` (no backends)
- `gpu_starvation_risk`: `low` (<3 queued), `medium` (3-7), `high` (8+)

### GET /stats

Detailed queue statistics: request counts (total, high, low), average response time, uptime.

### GET /backends

List all configured backends with connection status.

### GET /models?backend=local

List available models from a specific backend.

## Routing

The router selects which backend handles each request. Routing precedence:

1. **Explicit `backend=` parameter** — highest priority, bypasses all logic
2. **Complexity-based routing** (if `complexity` is set):
   - `ultra` — prefers `deepseek` > `cloud` > `local`
   - `high` — prefers `cloud` > `deepseek` > `local`
   - `low` — prefers `local`
3. **Priority-based routing** (legacy, if no complexity):
   - `high` + cloud available — uses `cloud`
4. **Default fallback** — always falls back to `default_backend`

The router **never fails** — if the preferred backend isn't available, it falls through to the default.

### Priority vs Complexity

These are orthogonal concepts:

- **Priority** = how urgent (queue ordering). HIGH jumps ahead of LOW in the queue.
- **Complexity** = how capable the backend needs to be (routing). ULTRA routes to the most powerful model.

A request can be `priority=low, complexity=ultra` (background task that needs a powerful model) or `priority=high, complexity=low` (interactive request that a small model can handle).

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
| `deepseek` | DeepseekClient | Bearer token | Deepseek cloud API |

### Environment Variable Overrides

All overridable with `OVERBLICK_GW_*` prefix:

| Variable | Purpose | Default |
|----------|---------|---------|
| `OVERBLICK_GW_API_PORT` | Gateway listen port | 8200 |
| `OVERBLICK_GW_API_HOST` | Gateway listen address | 0.0.0.0 |
| `OVERBLICK_GW_OLLAMA_HOST` | Ollama host | 127.0.0.1 |
| `OVERBLICK_GW_OLLAMA_PORT` | Ollama port | 11434 |
| `OVERBLICK_GW_DEFAULT_MODEL` | Default model | qwen3:8b |
| `OVERBLICK_GW_MAX_QUEUE_SIZE` | Max queued requests | 100 |
| `OVERBLICK_GW_REQUEST_TIMEOUT` | Per-request timeout (seconds) | 300 |
| `OVERBLICK_GW_MAX_CONCURRENT` | Max concurrent GPU requests | 1 |
| `OVERBLICK_GW_LOG_LEVEL` | Log verbosity | INFO |
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
)

response = await client.chat(
    messages=[{"role": "user", "content": "Hello"}],
    priority="high",
    complexity="ultra",
)
# response = {"content": "...", "model": "qwen3:8b", "tokens_used": 200, ...}

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
| `ollama_client.py` | Ollama/LM Studio HTTP client |
| `deepseek_client.py` | Deepseek API client |

## Testing

```bash
# Gateway unit tests
./venv/bin/python3 -m pytest tests/gateway/ -v

# Integration tests
./venv/bin/python3 -m pytest tests/integration/test_gateway_integration.py -v
```
