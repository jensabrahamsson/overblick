# Log Agent Plugin ("Vakt")

Multi-identity log monitoring agent for the Överblick framework. Scans log
files across all configured identities, detects error patterns, and alerts
the owner via Telegram. Built on the core `AgenticPluginBase` framework.

> **Status:** Functional in dry-run mode. Enable with `dry_run: false`
> in the Vakt personality YAML after validation.

## What It Does

- **Scans** log files for all configured identities every tick
- **Detects** ERROR and CRITICAL entries with traceback capture
- **Deduplicates** repeated errors to prevent alert fatigue
- **Analyzes** error patterns using LLM (via Gateway, `complexity="high"`)
- **Alerts** the owner via Telegram with severity-based formatting
- **Learns** from false positives to reduce noise over time

## Architecture

```
LogAgentPlugin (AgenticPluginBase)
  |
  |-- create_observer() ----------> _LogObserver
  |                                    \-> LogScanner.scan_all()
  |
  |-- get_action_handlers() -------> 4 handlers:
  |                                    |-> _ScanLogsHandler (no LLM)
  |                                    |-> _AnalyzePatternHandler (complexity="high")
  |                                    |-> _SendAlertHandler (dedup + Telegram)
  |                                    \-> _SkipHandler
  |
  \-- get_planning_prompt_config() -> PlanningPromptConfig
```

### The Agentic Loop

Each tick runs the five-phase cycle provided by `core/agentic/`:

1. **OBSERVE** — `_LogObserver` runs `LogScanner.scan_all()` across all
   configured identities. Returns a `LogObservation` with error counts,
   critical counts, and individual `LogEntry` objects.

2. **THINK** — The `ActionPlanner` receives the observation text and goals.
   Uses `complexity="ultra"` via Gateway (Deepseek for deep reasoning).

3. **PLAN** — Planner outputs up to 3 actions per tick. Valid action types:
   `scan_logs`, `analyze_pattern`, `send_alert`, `skip`.

4. **ACT** — `ActionExecutor` dispatches each action to its handler.

5. **REFLECT** — `ReflectionPipeline` evaluates outcomes against goals.
   Stores learnings in the agentic database (`complexity="low"`).

## Components

### LogScanner (`log_scanner.py`)

Incremental file scanner with:
- **Byte offset tracking** — only reads new data since last scan
- **File rotation detection** — resets offset when file shrinks
- **Deduplication** — removes duplicate `level:message` pairs per identity
- **Traceback capture** — appends stack traces following ERROR/CRITICAL lines

### AlertFormatter (`alerter.py`)

Formats scan results for Telegram:
- Severity-based headers (INFO, ERROR, CRITICAL)
- Per-identity breakdown with top 5 entries (truncated with "...and N more")
- Critical alert format with full traceback

### AlertDeduplicator (`alerter.py`)

Prevents alert spam:
- Tracks `identity:level:message` keys with cooldown period
- Default cooldown: 3600s (1 hour) — configurable
- `cleanup()` removes expired entries
- `should_alert()` returns False for duplicates within cooldown

## Configuration

Configured in the Vakt personality YAML (`identities/vakt/personality.yaml`):

```yaml
log_agent:
  scan_identities: ["anomal", "cherry", "blixt", "stal", "smed", "natt"]
  tick_interval_minutes: 5
  dry_run: true
  alerting:
    cooldown_seconds: 3600    # 1 hour between duplicate alerts
```

## LLM Routing

All LLM calls go through the Gateway:

| Action | LLM Usage | Complexity |
|--------|-----------|------------|
| `scan_logs` | None | N/A |
| `analyze_pattern` | Deep error analysis | `high` |
| `send_alert` | None | N/A |
| `skip` | None | N/A |
| Planning | Action selection | `ultra` (AgenticPluginBase default) |
| Reflection | Learning extraction | `low` (ReflectionPipeline default) |

## Testing

```bash
# Run all log agent tests
./venv/bin/python3 -m pytest tests/plugins/log_agent/ -v

# Test breakdown: 45 tests
# - test_log_scanner.py: 15 tests (file scanning, offsets, rotation, dedup)
# - test_alerter.py: 14 tests (formatting, severity, deduplication)
# - test_plugin.py: 16 tests (setup, observer, 4 action handlers)
```

## Inter-Agent Communication

Vakt can send messages to other agents via the Supervisor's MessageRouter:

```python
# Vakt → Smed: "Fix this bug"
await ctx.send_to_agent(
    target="smed",
    message_type="bug_report",
    payload={"identity": "anomal", "error": "LLM timeout"},
)
```

## Files

```
overblick/plugins/log_agent/
├── __init__.py
├── plugin.py          # LogAgentPlugin(AgenticPluginBase)
├── log_scanner.py     # Multi-identity incremental log scanner
├── alerter.py         # AlertFormatter + AlertDeduplicator
├── models.py          # LogEntry, LogScanResult, LogObservation, etc.
└── README.md          # This file
```
