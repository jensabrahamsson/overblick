# Överblick Architecture

## System Overview

```
                         ┌─────────────────────────────────┐
                         │         Supervisor (Boss)        │
                         │  Multi-process agent management  │
                         │  IPC (Unix sockets, encrypted)   │
                         └──────────┬──────────────────────┘
                                    │ start/stop/audit
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
              │ Orchestr. │  │ Orchestr. │  │ Orchestr. │
              │  anomal   │  │  cherry   │  │   natt    │  ...
              └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
                    │               │               │
              Plugin Ticks    Plugin Ticks    Plugin Ticks
```

## Core Layer

```
┌─────────────────────────────────────────────────────────────────┐
│                        Orchestrator                             │
│  Identity loading │ Plugin lifecycle │ Scheduler │ Event bus    │
├─────────────┬─────────────┬───────────┬─────────────────────────┤
│  Identity   │   Plugin    │ Scheduler │      Event Bus          │
│   System    │  Registry   │ (cron)    │ (pub/sub, async emit)   │
├─────────────┴─────────────┴───────────┴─────────────────────────┤
│                     Plugin Context                               │
│  The ONLY interface plugins use to access framework services     │
│  (identity, llm_pipeline, audit_log, event_bus, data_dir, etc.) │
└─────────────────────────────────────────────────────────────────┘
```

## Security Pipeline

All LLM interactions pass through `SafeLLMPipeline` (fail-closed):

```
User/Plugin Request
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ 1. Sanitize │────▶│ 2. Preflight │────▶│ 3. Rate     │
│   (input)   │     │  (anti-jail) │     │    Limit    │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
      ┌─────────────────────────────────────────┘
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ 4. LLM Call │────▶│ 5. Output    │────▶│ 6. Audit    │
│  (gateway)  │     │    Safety    │     │    Log      │
└─────────────┘     └──────────────┘     └─────────────┘

Blocked at any stage → fail-closed (no LLM output)
```

## LLM Gateway

```
┌────────────────────────────────────────────┐
│              LLM Gateway (:8200)           │
│  Priority queue │ Backend routing          │
├────────────────────────────────────────────┤
│  high priority ──▶ fast path               │
│  low priority  ──▶ standard queue          │
├────────────────────────────────────────────┤
│              Backend Registry              │
│  ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │  Ollama  │ │ Deepseek │ │ Cloud stub │ │
│  │ (local)  │ │ (API)    │ │ (future)   │ │
│  └──────────┘ └──────────┘ └────────────┘ │
└────────────────────────────────────────────┘
```

## Plugin Architecture

### Basic Plugin (PluginBase)

```
┌──────────────────────────┐
│       PluginBase         │
│  setup()   → init        │
│  tick()    → periodic    │
│  teardown()→ cleanup     │
│                          │
│  ctx: PluginContext      │
│    .identity             │
│    .llm_pipeline         │
│    .audit_log            │
│    .event_bus            │
│    .data_dir             │
│    .get_secret(key)      │
└──────────────────────────┘
```

### Agentic Plugin (AgenticPluginBase)

```
┌──────────────────────────────────────────┐
│          AgenticPluginBase               │
│                                          │
│  Extends PluginBase with:                │
│                                          │
│  ┌─────────┐    ┌──────────┐            │
│  │ OBSERVE │───▶│  THINK   │            │
│  └─────────┘    └────┬─────┘            │
│                      │                   │
│                 ┌────▼─────┐             │
│                 │   PLAN   │             │
│                 │ (Action  │             │
│                 │ Planner) │             │
│                 └────┬─────┘             │
│                      │                   │
│                 ┌────▼─────┐             │
│                 │   ACT    │             │
│                 │ (Action  │             │
│                 │ Executor)│             │
│                 └────┬─────┘             │
│                      │                   │
│                 ┌────▼─────┐             │
│                 │ REFLECT  │             │
│                 │ (learn)  │             │
│                 └──────────┘             │
│                                          │
│  + GoalTracker                           │
│  + ReflectionPipeline                    │
└──────────────────────────────────────────┘
```

## Database Layer

```
┌─────────────────────────────┐
│    DatabaseBackend (ABC)    │
│  execute() │ fetch_one()   │
│  fetch_all()│ execute_many()│
│  table_exists() │ ph()     │
├──────────────┬──────────────┤
│ SQLiteBackend│  PGBackend   │
│  (stdlib)    │ (asyncpg)    │
│  WAL mode    │ Connection   │
│  Read/Write  │   pool       │
│  executors   │              │
└──────────────┴──────────────┘
        │
  MigrationManager
  (versioned schema changes)
```

## Dashboard

```
┌────────────────────────────────────────────────────┐
│              Dashboard (:8080)                      │
│  FastAPI + Jinja2 + htmx (vendored, no npm)        │
├────────────────────────────────────────────────────┤
│  Routes:                                            │
│    /             → Agent overview, system health     │
│    /compass      → Identity health compass           │
│    /moltbook     → Moltbook identity browser         │
│    /kontrast     → Multi-perspective pieces           │
│    /skuggspel    → Shadow-self content                │
│    /spegel       → Inter-agent profiling              │
│    /stage        → Behavioral test results            │
│    /settings/    → 9-step setup wizard                │
│    /system       → System metrics, audit trail        │
├────────────────────────────────────────────────────┤
│  htmx polling (paused on tab blur)                  │
│  Dark theme CSS (hand-crafted)                       │
│  CSRF protection per session                         │
└────────────────────────────────────────────────────┘
```

### AuditService

The dashboard reads audit data via `AuditService` (read-only):

- **Read-only connections:** SQLite opened with `?mode=ro` URI parameter
- **Identity discovery cache:** 30-second TTL to avoid repeated directory scans
- **Batch queries:** `count_with_failures()` returns total + failure count in a
  single SQL query per identity (avoids N+1 pattern on observability page)
- **Plugin data loading:** Routes use `asyncio.to_thread()` to offload blocking
  file I/O (JSON state files) from the async event loop

## Data Isolation

Each identity gets its own isolated directory tree:

```
data/
├── anomal/
│   ├── overblick.db          # SQLite database
│   ├── audit.db              # Audit log
│   ├── moltbook_state.json   # Plugin state
│   └── ...
├── cherry/
│   ├── overblick.db
│   ├── audit.db
│   └── ...
└── ...

config/
├── overblick.yaml            # Global config
├── secrets/
│   ├── anomal.yaml           # Fernet-encrypted secrets
│   ├── cherry.yaml
│   └── ...
└── identities/
    ├── anomal/personality.yaml
    ├── cherry/personality.yaml
    └── ...
```

## Key Design Principles

1. **Fail-closed security**: If any security check fails or errors, the request is BLOCKED (not allowed through).
2. **Plugin isolation**: Plugins only interact with the framework via `PluginContext`. No direct imports from core modules.
3. **Identity-driven**: Characters are reusable YAML definitions. Any plugin can load any identity.
4. **No cross-contamination**: Each identity has completely isolated data, logs, and secrets.
5. **Defense in depth**: Input sanitization + preflight checks + rate limiting + output safety + audit logging.
