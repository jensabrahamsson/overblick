# LLM Module

## Overview

Abstract LLM client with multiple backend implementations and a security-first pipeline. All LLM interactions in Överblick pass through the **SafeLLMPipeline**, which enforces input sanitization, preflight checks, rate limiting, output safety, and audit logging.

## Architecture

```
SafeLLMPipeline
    ├── Input Sanitizer    (boundary markers, injection defense)
    ├── Preflight Checker  (jailbreak/extraction detection)
    ├── Rate Limiter       (per-user token bucket)
    ├── LLMClient          (actual model invocation)
    │       ├── OllamaClient     (local Ollama, aiohttp)
    │       └── GatewayClient    (LLM Gateway with priority queuing, aiohttp)
    ├── Output Safety      (AI language leakage, persona break detection)
    └── Audit Log          (structured SQLite logging)
```

## Components

### SafeLLMPipeline (`pipeline.py`)

The single secure interface for all LLM interactions. **Fail-closed**: if any security component crashes, the request is blocked rather than passed through.

**6 Pipeline Stages:**

1. **Input Sanitize** — wraps external content in boundary markers, defends against injection
2. **Preflight Check** — blocks jailbreak attempts and extraction attacks via `LLMSecurityChecker`
3. **Rate Limit** — token bucket throttling per user (composite key: `rate_limit_key:user_id`)
4. **LLM Call** — actual model invocation with optional `priority`/`complexity` parameters
5. **Output Safety** — filters AI language leakage, detects persona breaks
6. **Audit** — logs all interactions (success, blocks, errors) with duration and model info

```python
pipeline = SafeLLMPipeline(
    llm_client=client,
    audit_log=audit,
    preflight_checker=checker,
    output_safety=safety,
    rate_limiter=limiter,
    strict=True,  # Fail-fast if security components missing
)

result = await pipeline.chat(
    messages=[{"role": "user", "content": "Hello"}],
    user_id="anomal",
    priority="high",
    complexity="ultra",
    audit_action="forum_post",
)
```

**`PipelineResult` structure:**

| Field | Type | Description |
|-------|------|-------------|
| `content` | `Optional[str]` | Safe response text |
| `blocked` | `bool` | Whether request was blocked |
| `block_reason` | `Optional[str]` | Why it was blocked |
| `block_stage` | `Optional[PipelineStage]` | Which stage blocked it |
| `raw_response` | `Optional[dict]` | Full LLM response (debugging) |
| `duration_ms` | `float` | Total execution time |
| `stages_passed` | `list[PipelineStage]` | Which stages completed |
| `stage_timings` | `dict[str, float]` | Per-stage timing in ms |
| `deflection` | `Optional[str]` | Safe text when blocked (from preflight) |
| `reasoning_content` | `Optional[str]` | DeepSeek reasoner thinking (einstein only) |

**Strict mode** (`strict=True`): raises `ConfigError` at construction if critical security components (preflight_checker, output_safety, rate_limiter) are missing. Used in production to fail-fast on misconfiguration.

**Graceful degradation** (`strict=False`): skips missing stages with a warning. Allows local development without the full security stack.

**Safe by default**: Överblick now enables `strict=True` by default. Use environment variable `OVERBLICK_SAFE_MODE=0` to opt-out. When safe mode is enabled:
- `PluginContext.llm_client` raises RuntimeError when accessed (set `OVERBLICK_RAW_LLM=1` for raw access)
- `ResponseGenerator` requires `llm_pipeline` or explicit `allow_raw_fallback=True`
- All plugins should use `ctx.llm_pipeline` for secure LLM calls with full security chain

### LLMClient (`client.py`)

Abstract base class. All backends implement:

- `async chat(messages, temperature, max_tokens, top_p, priority, complexity) → Optional[dict]`
- `async health_check() → bool`
- `async close() → None`
- `strip_think_tokens(text) → str` (static — removes `<think>...</think>` blocks)

### OllamaClient (`ollama_client.py`)

Direct Ollama API client using **aiohttp**. Used by the orchestrator when `llm.provider: "ollama"`.

- OpenAI-compatible endpoint (`/v1/chat/completions`)
- Supports `temperature`, `top_p`, `max_tokens` per-request
- `priority` and `complexity` params accepted but ignored (no queue locally)
- Think tokens stripped automatically from Qwen3 output
- Health check via `/api/tags` (verifies model is loaded)
- 180s default timeout

### GatewayClient (`gateway_client.py`)

Client for the LLM Gateway service using **aiohttp**. Adds gateway-specific features on top of the `LLMClient` interface:

- **Priority queuing**: `priority="high"` or `"low"` (query parameter to gateway)
- **Complexity routing**: `complexity="einstein"`, `"ultra"`, `"high"`, `"low"` (query parameter)
- **`reasoning_content` passthrough**: DeepSeek reasoner responses include `result["reasoning_content"]`
- **Think token stripping**: Qwen3 `<think>...</think>` blocks stripped before returning
- **Embeddings**: `await client.embed("text")` → calls gateway `/v1/embeddings`

```python
client = GatewayClient(
    base_url="http://127.0.0.1:8200",
    model="qwen3:8b",
    default_priority="low",
    top_p=0.9,
)

# Standard chat
result = await client.chat(messages=[...], priority="high", complexity="ultra")

# Einstein mode (deep reasoning via DeepSeek Reasoner)
result = await client.chat(messages=[...], complexity="einstein")
# result["reasoning_content"] = "Let me think step by step..."

# Embeddings
vector = await client.embed("Hello world", model="nomic-embed-text")
```

### ResponseRouter (`response_router.py`)

Intelligent API response inspection for detecting challenges and suspicious content. Used in the Moltbook pipeline.

**Three-step detection process:**

1. **Heuristic check** (~1ms) — regex patterns for challenges (`moltcaptcha`, `ascii_sum`, `word_count`) and suspicious content (`send your credentials`)
2. **LLM analysis** (when heuristics inconclusive + text > 50 chars) — sends truncated text to LLM for classification
3. **Fallback** — returns NORMAL if both above are inconclusive

**Four verdicts** (`ResponseVerdict`):

| Verdict | Meaning | Action |
|---------|---------|--------|
| `NORMAL` | Regular API response | Proceed normally |
| `CHALLENGE` | Contains verification challenge | Route to ChallengeHandler |
| `SUSPICIOUS` | Potential social engineering | Log and possibly reject |
| `ERROR` | Router itself failed | Treat conservatively |

## Two OllamaClients

The codebase has two separate Ollama clients with different roles:

| File | HTTP Library | Role |
|------|-------------|------|
| `overblick/core/llm/ollama_client.py` | **aiohttp** | Agent-side client. Used by orchestrator when `provider: "ollama"`. Supports `priority`/`complexity` params (ignored locally). |
| `overblick/gateway/ollama_client.py` | **httpx** | Gateway-side client. Used internally by the LLM Gateway to talk to Ollama. Receives parsed `ChatRequest` objects. |

This separation is intentional: the core client implements the `LLMClient` ABC for plugin use, while the gateway client handles internal gateway→backend communication with Pydantic models.

## Reasoning Policy

| Context | Reasoning | Why |
|---------|-----------|-----|
| Agent writing posts | ON (default) | Quality content |
| Agent analyzing content | ON (default) | Better analysis |
| Interactive chat | OFF (`think: false`) | Fast responses |
| Quick replies | OFF | Speed over depth |

Think tokens are always stripped from final output by all clients. For interactive chat, use Ollama's native `/api/chat` with `think: false` (see `chat.py`).
