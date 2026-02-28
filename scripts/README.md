# Överblick Manager

Unified management script for the entire Överblick platform. Controls the LLM Gateway, web dashboard, and multi-agent supervisor from a single interface.

## Location

```
scripts/overblick_manager.sh
```

## Prerequisites

- Python virtual environment at `venv/` (with Överblick installed)
- Optional: `config/.env` for API keys (e.g. `OVERBLICK_DEEPSEEK_API_KEY`)

The script automatically sources `config/.env` before launching any component.

## Quick Start

```bash
# Start everything (gateway, dashboard, supervisor with all default identities)
./scripts/overblick_manager.sh up

# Stop everything gracefully
./scripts/overblick_manager.sh down

# Full restart
./scripts/overblick_manager.sh restart

# Check status of all components
./scripts/overblick_manager.sh status
```

## Platform Commands

| Command | Description |
|---------|-------------|
| `up [IDENTITIES]` | Start gateway, dashboard, and supervisor (in order) |
| `down` | Stop supervisor, dashboard, and gateway (reverse order) |
| `restart [IDENTITIES]` | Full stop + start cycle with 2s grace period |
| `status` | Show status of all components and running agents |

**Default identities:** `anomal cherry natt stal vakt`

Override by passing a quoted string:
```bash
./scripts/overblick_manager.sh up "anomal cherry"
```

## Startup Order

`up` starts components in dependency order:

1. **Gateway** (port 8200) — LLM routing backend (Ollama, DeepSeek, cloud)
2. **Dashboard** (port 8080) — FastAPI web UI for monitoring
3. **Supervisor** — spawns and monitors all agent identities

`down` reverses this order for clean shutdown.

## Component Commands

### Gateway

The LLM Gateway routes requests to local (Ollama), cloud (LM Studio), and DeepSeek backends.

```bash
./scripts/overblick_manager.sh gateway start
./scripts/overblick_manager.sh gateway stop
./scripts/overblick_manager.sh gateway restart
./scripts/overblick_manager.sh gateway status
./scripts/overblick_manager.sh gateway logs      # tail -f
```

- Listens on `http://localhost:8200`
- Health check: `curl http://localhost:8200/health`
- Waits up to 10s for health confirmation on start
- Automatically kills stray gateway processes before starting

### Dashboard

Web-based monitoring UI (FastAPI + htmx).

```bash
./scripts/overblick_manager.sh dashboard start
./scripts/overblick_manager.sh dashboard stop
./scripts/overblick_manager.sh dashboard restart
./scripts/overblick_manager.sh dashboard status
./scripts/overblick_manager.sh dashboard logs

# Custom port
./scripts/overblick_manager.sh dashboard start --port 9090
```

- Default port: `8080`
- Waits up to 8s for HTTP confirmation on start

### Supervisor

The supervisor manages multiple agent identities as child processes, with IPC via authenticated Unix sockets.

```bash
./scripts/overblick_manager.sh supervisor-start "anomal cherry natt stal"
./scripts/overblick_manager.sh supervisor-stop
./scripts/overblick_manager.sh supervisor-restart "anomal cherry natt stal"
./scripts/overblick_manager.sh supervisor-status
./scripts/overblick_manager.sh supervisor-logs
```

- Guards against duplicate supervisors (refuses to start if one is already running)
- `supervisor-stop` kills all supervisor processes (PID file + stray detection via `pgrep`)
- `supervisor-status` warns if multiple instances are detected

## Individual Identity Commands

Run a single identity outside the supervisor (useful for development/debugging):

```bash
./scripts/overblick_manager.sh start anomal
./scripts/overblick_manager.sh stop anomal
./scripts/overblick_manager.sh restart anomal
./scripts/overblick_manager.sh status anomal
./scripts/overblick_manager.sh logs anomal
```

**Note:** When using the supervisor, individual identity commands are not needed — the supervisor manages all agents.

## File Layout

| Path | Purpose |
|------|---------|
| `data/<identity>/overblick.pid` | PID file for standalone identity |
| `data/gateway/gateway.pid` | Gateway PID |
| `data/dashboard/dashboard.pid` | Dashboard PID |
| `data/supervisor/supervisor.pid` | Supervisor PID |
| `logs/<identity>/overblick.log` | Per-identity log |
| `logs/gateway/gateway.log` | Gateway log |
| `logs/dashboard/dashboard.log` | Dashboard log |
| `logs/supervisor/overblick.log` | Supervisor log |
| `config/.env` | Environment variables (API keys, gitignored) |

## Process Management

The manager uses a belt-and-suspenders approach to prevent orphaned processes:

1. **PID files** — each component writes its PID on start
2. **Stray detection** — `pgrep -f` scans for unmanaged processes before starting
3. **Graceful shutdown** — `SIGTERM` first, waits up to 10s, then `SIGKILL` if needed
4. **Stale cleanup** — removes PID files when the process is no longer running

## Environment Variables

The script sources `config/.env` (if it exists) before launching any component. This is the recommended way to pass API keys:

```bash
# config/.env
OVERBLICK_DEEPSEEK_API_KEY=sk-your-key-here
```

This file is gitignored and never committed.

## Examples

```bash
# Start only Anomal and Cherry
./scripts/overblick_manager.sh up "anomal cherry"

# Restart just the gateway (after config change)
./scripts/overblick_manager.sh gateway restart

# Follow Stål's live log
./scripts/overblick_manager.sh logs stal

# Check if supervisor has duplicate instances
./scripts/overblick_manager.sh supervisor-status

# Full platform status (gateway, dashboard, supervisor, all agents)
./scripts/overblick_manager.sh status
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `status` shows STOPPED but processes are running | Supervisor-managed agents don't create individual PID files. Use `status` (platform-level) which detects running processes via `pgrep`. |
| Gateway fails health check | Check `logs/gateway/gateway.log`. Verify Ollama is running (`ollama list`). |
| Duplicate supervisor warning | Run `supervisor-stop` then `supervisor-start` to clean up. |
| Port 8080 already in use | Use `dashboard start --port 9090` or stop the conflicting process. |
| "Virtual environment not found" | Run `python3.13 -m venv venv && venv/bin/pip install -e .` |
