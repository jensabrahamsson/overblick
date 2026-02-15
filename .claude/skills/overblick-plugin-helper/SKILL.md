---
name: overblick-plugin-helper
description: Guide for creating, reviewing, and debugging Överblick plugins
triggers:
  - create plugin
  - new plugin
  - review plugin
  - debug plugin
  - plugin structure
  - add plugin
  - scaffold plugin
  - plugin architecture
---

# Överblick Plugin Helper

You are helping a developer work with plugins in the **Överblick** agent framework. Plugins are self-contained modules that receive `PluginContext` as their ONLY interface to the framework. This ensures clean isolation between plugins and the core.

## Plugin Lifecycle

Every plugin follows this lifecycle:

```
__init__(ctx: PluginContext) → setup() → tick() [repeated] → teardown()
```

1. **`__init__(ctx)`** — Store the context reference. Do NOT perform I/O here.
2. **`setup()`** — Async. Initialize components: load secrets, create clients, setup capabilities. Raise `RuntimeError` to prevent plugin from starting.
3. **`tick()`** — Async. Called periodically by the scheduler. Should be quick — spawn tasks for long work.
4. **`teardown()`** — Async. Clean up resources (close clients, stop tasks). Optional override.

## Creating a New Plugin

When the user asks to create a plugin, follow this interactive process:

### Step 1: Gather Requirements
Ask the user:
- **Plugin name** (lowercase, no spaces — e.g., `slack`, `matrix`, `webhook`)
- **Purpose** (what does the plugin do?)
- **External dependencies** (APIs, libraries, services)
- **Does it need LLM?** (most plugins do — use `ctx.llm_pipeline`, never raw client)
- **Does it need capabilities?** (conversation tracking, engagement analysis, etc.)

### Step 2: Scaffold the Plugin

Create these files:

```
overblick/plugins/<name>/
├── __init__.py          # Re-export plugin class
├── plugin.py            # Main plugin class (extends PluginBase)
tests/plugins/<name>/
├── __init__.py
├── conftest.py          # Test fixtures (mock context, identity, etc.)
├── test_<name>.py       # Plugin tests
```

#### `overblick/plugins/<name>/__init__.py`
```python
from overblick.plugins.<name>.plugin import <Name>Plugin

__all__ = ["<Name>Plugin"]
```

#### `overblick/plugins/<name>/plugin.py` — Template
```python
"""
<Name>Plugin — <brief description>.

<Detailed description of what this plugin does, its features,
and security properties.>
"""

import logging
from typing import Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class <Name>Plugin(PluginBase):
    """<Brief description>."""

    name = "<name>"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        # Initialize state (no I/O here)
        self._client: Optional[...] = None

    async def setup(self) -> None:
        """Initialize plugin components using self.ctx."""
        identity = self.ctx.identity
        logger.info("Setting up <Name>Plugin for identity: %s", identity.name)

        # Load secrets
        api_key = self.ctx.get_secret("<name>_api_key")
        if not api_key:
            raise RuntimeError(f"Missing <name>_api_key for {identity.name}")

        # Audit the setup
        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )
        logger.info("<Name>Plugin setup complete for %s", identity.name)

    async def tick(self) -> None:
        """Main work cycle — called periodically by scheduler."""
        # Check quiet hours
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        # Do work here...

    async def teardown(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.close()
        logger.info("<Name>Plugin teardown complete")
```

### Step 3: Register in Plugin Registry

Edit `overblick/core/plugin_registry.py` — add to `_KNOWN_PLUGINS`:

```python
_KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    # ... existing plugins ...
    "<name>": ("overblick.plugins.<name>.plugin", "<Name>Plugin"),
}
```

**CRITICAL:** Plugins NOT in `_KNOWN_PLUGINS` cannot be loaded. This is a security whitelist.

### Step 4: Create Test Fixtures

See `references/plugin-examples.md` for full conftest patterns.

#### `tests/plugins/<name>/conftest.py` — Template
```python
"""Test fixtures for <Name>Plugin."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.personalities import Identity
from overblick.core.plugin_base import PluginContext
from overblick.core.llm.pipeline import SafeLLMPipeline, PipelineResult


@pytest.fixture
def <name>_identity():
    """Mock identity for <name> plugin."""
    identity = MagicMock(spec=Identity)
    identity.name = "test"
    identity.raw_config = {}
    identity.llm = MagicMock()
    identity.llm.temperature = 0.7
    identity.llm.max_tokens = 500
    return identity


@pytest.fixture
def <name>_context(<name>_identity, tmp_path, mock_llm_client, mock_audit_log):
    """Full plugin context for <name> tests."""
    pipeline = AsyncMock(spec=SafeLLMPipeline)
    pipeline.chat = AsyncMock(return_value=PipelineResult(content="Test response"))

    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=pipeline,
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=<name>_identity,
    )
    ctx._secrets_getter = lambda k: {"<name>_api_key": "test-key"}.get(k)
    return ctx
```

### Step 5: Write Tests and Verify

```bash
# Run plugin tests
./venv/bin/python3 -m pytest tests/plugins/<name>/ -v

# Run all tests to check nothing broke
./venv/bin/python3 -m pytest tests/ -v -m "not llm" -x
```

## Reviewing a Plugin

When asked to review a plugin, check against the checklist in `references/plugin-checklist.md`. Key areas:

1. **Security** — External content wrapped? Pipeline used (not raw client)? Secrets via `get_secret()`?
2. **Quality** — Tests exist? Type hints? English code? Teardown cleanup?
3. **Architecture** — Registered in `_KNOWN_PLUGINS`? Uses `PluginContext` correctly?

## PluginContext Quick Reference

| Field | Type | Description |
|-------|------|-------------|
| `identity_name` | `str` | Current identity name |
| `data_dir` | `Path` | Isolated data directory (auto-created) |
| `log_dir` | `Path` | Log directory (auto-created) |
| `llm_client` | `Any` | Raw LLM client (prefer `llm_pipeline`) |
| `llm_pipeline` | `Any` | SafeLLMPipeline (preferred for LLM calls) |
| `event_bus` | `Any` | EventBus for pub/sub |
| `scheduler` | `Any` | Task scheduler |
| `audit_log` | `Any` | AuditLog for recording actions |
| `quiet_hours_checker` | `Any` | Check if in quiet hours |
| `identity` | `Any` | Full Identity config object |
| `engagement_db` | `Any` | Per-identity engagement database |
| `permissions` | `Any` | Permission checker |
| `capabilities` | `dict` | Shared capabilities from orchestrator |
| `get_secret(key)` | method | Get decrypted secret by key |

## Key Files

| File | Purpose |
|------|---------|
| `overblick/core/plugin_base.py` | `PluginBase` + `PluginContext` definitions |
| `overblick/core/plugin_registry.py` | `_KNOWN_PLUGINS` whitelist + `PluginRegistry` |
| `overblick/core/orchestrator.py` | Wires context and runs plugins |
| `overblick/core/security/input_sanitizer.py` | `wrap_external_content()` |
| `overblick/core/security/audit_log.py` | `AuditLog` class |
| `overblick/core/llm/pipeline.py` | `SafeLLMPipeline` |

## References

- `references/plugin-architecture.md` — Full API details for all framework components
- `references/plugin-checklist.md` — Security and quality checklist
- `references/plugin-examples.md` — Condensed real plugin examples (Telegram, Gmail, Moltbook)
