# System Capabilities

Core infrastructure capabilities automatically injected into all agents by the orchestrator. These are not opt-in — every agent receives them.

## Capabilities

### `system_clock`

Time awareness for agents. Provides access to the current system time in the agent's configured timezone.

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `now()` | `datetime` | Current datetime (timezone-aware) |
| `date_str()` | `str` | Date as `YYYY-MM-DD` |
| `time_str()` | `str` | Time as `HH:MM` |
| `weekday()` | `str` | English weekday name |
| `iso()` | `str` | Full ISO 8601 timestamp |
| `get_prompt_context()` | `str` | Human-readable time for LLM injection |

**Timezone:** Reads from `identity.quiet_hours.timezone`, defaults to `Europe/Stockholm`.

**LLM Integration:** The `get_prompt_context()` method returns a string like:
```
Current time: Saturday, February 15, 2026 at 16:45 (CET)
```
This can be injected into system prompts so agents know the current time.

## Usage

```python
# Access via capability registry (auto-available on all agents)
clock = ctx.get_capability("system_clock")
print(clock.now())        # 2026-02-15 16:45:00+01:00
print(clock.weekday())    # Saturday
print(clock.date_str())   # 2026-02-15
```

## Architecture

- **Auto-injection:** The orchestrator adds `system_clock` to every agent's capability list in `_setup_capabilities()`, regardless of what the identity config specifies.
- **No secrets required:** Reads only the system clock and timezone config.
- **No external dependencies:** Pure Python `datetime` + `zoneinfo`.

## Testing

```bash
pytest tests/capabilities/test_system_clock.py -v
```
