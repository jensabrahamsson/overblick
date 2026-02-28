# Supervisor

## Overview

Multi-process boss agent that manages all identity agent processes. Communicates via authenticated Unix domain sockets (IPC). Provides centralized permission management, health monitoring, and inter-agent message routing.

## Architecture

```
Supervisor
├── IPCServer          — Unix socket server with auth + rate limiting
├── AgentProcess       — Subprocess lifecycle management
├── MessageRouter      — Inter-agent message routing
├── PermissionManager  — Default-deny permission system
├── SupervisorAudit    — Action audit trail
└── Handlers
    ├── HealthHandler   — Health check aggregation
    ├── EmailHandler    — Email routing to Stål
    └── ResearchHandler — Research request routing
```

## Components

### Supervisor (`supervisor.py`)

Main supervisor class. Starts IPC server, launches agent processes, and orchestrates shutdown. Startup failures are caught and cleaned up (IPC server is stopped if agent startup fails).

### IPC (`ipc.py`)

Unix domain socket communication with JSON protocol:
- **Authentication**: Fernet-encrypted tokens shared via file (mode 0o600)
- **Rate limiting**: Per-sender sliding window (100/min default, 1000 max tracked senders)
- **Message size limit**: 1 MB max to prevent OOM
- **Socket permissions**: Owner-only (0o600)

### AgentProcess (`process.py`)

Subprocess wrapper for individual agent processes. Manages PID tracking, health monitoring, and graceful shutdown.

### Message Router (`routing.py`)

Routes messages between agents. Agents register capabilities; the router dispatches requests to the appropriate handler.

## IPC Protocol

```json
{
    "type": "status_request",
    "payload": {},
    "sender": "anomal",
    "timestamp": "2026-02-28T12:00:00",
    "request_id": "abc123",
    "auth_token": "hex-token"
}
```

## Security

- **Auth tokens**: Generated with `secrets.token_hex(32)`, encrypted at rest with Fernet
- **HMAC validation**: Constant-time comparison via `hmac.compare_digest()`
- **Socket permissions**: Directory 0o700, socket file 0o600
- **Rate limiting**: Per-sender with LRU eviction of inactive senders
- **Token cleanup**: Token file removed on supervisor stop

## Running

```bash
# Start supervisor with specific identities
./scripts/overblick_manager.sh supervisor-start "anomal cherry natt stal"

# Stop all agents
./scripts/overblick_manager.sh supervisor-stop

# Check status
./scripts/overblick_manager.sh supervisor-status
```
