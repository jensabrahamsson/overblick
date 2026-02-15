# Monitoring Capabilities

## Overview

The **monitoring** bundle provides secure system health inspection for agent plugins. It enables agents to collect host metrics (memory, CPU, disk, uptime, battery) using only whitelisted commands — no arbitrary shell execution allowed.

This bundle powers the Supervisor's health inquiry handler and the host_health plugin, where agents like Natt ask about the state of the machine they inhabit.

## Capabilities

### HostInspectionCapability

Secure system health data collection using an immutable whitelist of allowed executables. Cross-platform (macOS and Linux) with concurrent data collection and graceful partial failure handling.

**Registry name:** `host_inspection`

## Methods

### HostInspectionCapability

```python
async def inspect(self) -> HostHealth:
    """
    Collect system health data from whitelisted commands.

    Returns:
        HostHealth snapshot with memory, CPU, disk, uptime,
        power info (macOS), hostname, platform, and any errors.
    """
```

**Whitelisted commands (immutable frozenset):**
- `vm_stat`, `sysctl`, `df`, `ps`, `uptime`, `pmset` (macOS)
- `free`, `nproc`, `cat`, `hostname` (Linux)

No other executables can be invoked. This is enforced at the code level.

## Data Models

### HostHealth

Complete system health snapshot:

```python
class HostHealth(BaseModel):
    hostname: str
    platform: str            # "darwin" or "linux"
    memory: MemoryInfo | None
    cpu: CPUInfo | None
    disks: list[DiskInfo]
    uptime: str | None
    power: PowerInfo | None  # macOS only
    errors: list[str]        # Errors from failed collectors
    timestamp: str           # ISO 8601

    @property
    def health_grade(self) -> str:
        """Calculate 'good', 'fair', or 'poor' based on thresholds."""

    def to_summary(self) -> str:
        """Multi-line text summary suitable for LLM prompts."""
```

### Health Grade Thresholds

| Metric | Fair (+1 issue) | Poor (+2 issues) |
|--------|-----------------|-------------------|
| Memory | > 75% used | > 90% used |
| CPU | load > core count | load > 2x core count |
| Disk | > 85% used (per mount) | > 95% used (per mount) |

**Grading:** 0 issues = "good", 1-2 issues = "fair", 3+ issues = "poor"

### Supporting Models

```python
class MemoryInfo(BaseModel):
    total_mb: float
    used_mb: float
    available_mb: float
    percent_used: float

class CPUInfo(BaseModel):
    load_1m: float
    load_5m: float
    load_15m: float
    core_count: int

class DiskInfo(BaseModel):
    mount: str
    total_gb: float
    used_gb: float
    available_gb: float
    percent_used: float

class PowerInfo(BaseModel):       # macOS only
    on_battery: bool
    battery_percent: float | None
    time_remaining: str | None
```

### IPC Models

```python
class HealthInquiry(BaseModel):
    sender: str               # Agent identity name
    motivation: str           # Why the agent is asking
    timestamp: str
    previous_context: str     # Last conversation summary

class HealthResponse(BaseModel):
    responder: str            # "supervisor" or "anomal"
    response_text: str        # LLM-crafted response
    health_grade: str         # "good", "fair", "poor"
    timestamp: str
    health_summary: str       # Raw health data summary
```

## Plugin Integration

Plugins access the monitoring capability through `CapabilityContext`:

```python
from overblick.capabilities.monitoring import HostInspectionCapability

class MyPlugin(PluginBase):
    async def setup(self) -> None:
        self.inspector = HostInspectionCapability(self.ctx)
        await self.inspector.setup()

    async def check_health(self):
        health = await self.inspector.inspect()
        print(f"Grade: {health.health_grade}")
        print(health.to_summary())
```

Or via the Supervisor's health inquiry IPC (recommended for non-Supervisor agents):

```python
# Agent sends health inquiry via IPC
response = await ipc_client.send(IPCMessage(
    msg_type="health_inquiry",
    payload={
        "sender": "natt",
        "motivation": "I wonder about the state of my host.",
    },
))
```

## Security

### Whitelist Enforcement
- Only commands from an immutable `frozenset` can execute
- No shell interpretation (`asyncio.create_subprocess_exec`, never `shell=True`)
- 5s timeout per command prevents hangs
- Each collector runs in isolation — partial failures return partial data

### Why Whitelisting Matters
Agents could theoretically be manipulated via prompt injection to request system commands. The whitelist ensures that even if an agent's LLM is compromised, it cannot execute arbitrary commands. Only safe, read-only system inspection tools are permitted.

## Configuration

The monitoring bundle requires no configuration. It auto-detects the platform (macOS vs Linux) and uses appropriate commands.

```yaml
capabilities:
  - monitoring  # Expands to: host_inspection
```

## Testing

```bash
# Monitoring capability tests
pytest tests/capabilities/monitoring/ -v

# Specific test
pytest tests/capabilities/monitoring/test_inspector.py -v
```

## Related Bundles

- **communication** — Email, Telegram, boss requests
- **content** — Text summarization
- **engagement** — Content analysis and response generation
