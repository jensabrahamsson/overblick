# Plugin Quick-Start Guide

Create a new Överblick plugin in 5 steps.

## 1. Create the Plugin Directory

```
overblick/plugins/my_plugin/
    __init__.py
    plugin.py
    models.py       # optional — Pydantic models for state
    README.md       # required — document purpose and config
```

## 2. Implement the Plugin Class

```python
"""
MyPlugin — brief one-line description.

Longer description of what the plugin does, how it works,
and any external dependencies.
"""

import logging
import time
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


class MyPlugin(PluginBase):
    """
    One-paragraph description of this plugin's purpose.

    Lifecycle:
        setup()    — Load config and restore state
        tick()     — Periodic work
        teardown() — Persist state
    """

    name = "my_plugin"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._last_run: float = 0.0

    async def setup(self) -> None:
        """Initialize — load config from identity YAML."""
        config = self.ctx.identity.raw_config.get("my_plugin", {})
        # Read configuration with sensible defaults
        self._interval_hours = config.get("interval_hours", 12)

        # Use the isolated data directory for state files
        self._state_file = self.ctx.data_dir / "my_plugin_state.json"

        # Audit the setup
        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name},
        )
        logger.info("MyPlugin setup complete")

    async def tick(self) -> None:
        """Called periodically by the scheduler."""
        if not self._is_run_time():
            return

        self._last_run = time.time()

        # Access the LLM via the safe pipeline (NEVER use llm_client directly)
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            logger.warning("MyPlugin: no LLM pipeline available")
            return

        result = await pipeline.chat(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            audit_action="my_plugin_chat",
        )

        if result.blocked:
            logger.warning("MyPlugin: LLM blocked: %s", result.block_reason)
            return

        logger.info("MyPlugin: got response: %s", result.content[:100])

    async def teardown(self) -> None:
        """Persist state on shutdown."""
        logger.info("MyPlugin teardown complete")

    def _is_run_time(self) -> bool:
        if self._last_run == 0.0:
            return True
        return (time.time() - self._last_run) >= self._interval_hours * 3600
```

## 3. Register in the Plugin Registry

Add your plugin to `_DEFAULT_PLUGINS` in `overblick/core/plugin_registry.py`:

```python
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    # ... existing plugins ...
    "my_plugin": ("overblick.plugins.my_plugin.plugin", "MyPlugin"),
}
```

## 4. Configure in Personality YAML

Add config to the identity YAML that should run your plugin:

```yaml
# config/identities/anomal/personality.yaml
plugins:
  - my_plugin

my_plugin:
  interval_hours: 12
```

## 5. Write Tests

Create `tests/plugins/my_plugin/test_my_plugin.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.core.plugin_base import PluginContext
from overblick.plugins.my_plugin.plugin import MyPlugin


@pytest.fixture
def ctx(tmp_path):
    identity = MagicMock()
    identity.name = "test"
    identity.raw_config = {"my_plugin": {"interval_hours": 1}}

    return PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        identity=identity,
        audit_log=MagicMock(),
        llm_pipeline=AsyncMock(),
    )


@pytest.fixture
def plugin(ctx):
    return MyPlugin(ctx)


@pytest.mark.asyncio
async def test_setup(plugin):
    await plugin.setup()
    assert plugin._interval_hours == 1


@pytest.mark.asyncio
async def test_tick_skips_when_not_run_time(plugin):
    await plugin.setup()
    plugin._last_run = 9999999999.0  # far future
    await plugin.tick()
    plugin.ctx.llm_pipeline.chat.assert_not_called()
```

## Key Rules

1. **Use `PluginContext` only** — never import framework modules directly
2. **Use `llm_pipeline`** — never call `llm_client.chat()` directly (bypasses security)
3. **Wrap external content** — use `wrap_external_content()` for any user/RSS/API data sent to LLM
4. **Use `ctx.get_secret()`** — never hardcode credentials
5. **Audit important actions** — call `ctx.audit_log.log()` for significant operations
6. **State in `ctx.data_dir`** — all persistent state goes in the identity-isolated data directory
7. **Write a README.md** — document purpose, config options, and testing instructions

## PluginContext Quick Reference

| Property | Type | Description |
|----------|------|-------------|
| `ctx.identity` | Identity | Loaded identity object |
| `ctx.identity_name` | str | Identity name (e.g. "anomal") |
| `ctx.data_dir` | Path | Isolated data directory for this identity |
| `ctx.log_dir` | Path | Isolated log directory |
| `ctx.llm_pipeline` | SafeLLMPipeline | Secure LLM interface (preferred) |
| `ctx.llm_client` | LLMClient | Raw LLM client (avoid in plugins) |
| `ctx.audit_log` | AuditLog | Action audit logger |
| `ctx.event_bus` | EventBus | Pub/sub event system |
| `ctx.scheduler` | Scheduler | Periodic task scheduler |
| `ctx.get_secret(key)` | str/None | Encrypted secrets access |
| `ctx.get_capability(name)` | Any/None | Shared capabilities |
| `ctx.load_identity(name)` | Identity | Load another identity |
| `ctx.build_system_prompt(identity)` | str | Build LLM system prompt |
| `ctx.send_to_agent(target, type, payload)` | dict/None | IPC to other agents |
| `ctx.collect_messages()` | list[dict] | Receive IPC messages |

## Agentic Plugins

For autonomous agents (GitHub Agent, Dev Agent, Log Agent), extend `AgenticPluginBase` instead:

```python
from overblick.core.agentic.base import AgenticPluginBase

class MyAgentPlugin(AgenticPluginBase):
    name = "my_agent"

    # Inherits the full OBSERVE/THINK/PLAN/ACT/REFLECT loop
    # Override observe(), think(), plan(), act(), reflect()
```

See `overblick/plugins/github/plugin.py` for a complete example.

## Dashboard Routes for Plugins

If your plugin has a dashboard page, the route handler loads state files from
the data directory. Use `asyncio.to_thread()` to avoid blocking the event loop:

```python
import asyncio

@router.get("/my-plugin", response_class=HTMLResponse)
async def my_plugin_page(request: Request):
    data = await asyncio.to_thread(_load_state, request)
    return templates.TemplateResponse("my_plugin.html", {"data": data})

def _load_state(request: Request) -> list:
    """Synchronous file I/O — runs in a thread pool."""
    data_root = resolve_data_root(request)
    # ... read JSON files, scan directories ...
    return results
```

Keep the blocking I/O in a plain `def` function and call it with
`asyncio.to_thread()` from the `async def` route handler.
