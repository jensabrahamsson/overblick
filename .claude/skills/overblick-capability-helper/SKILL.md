---
name: overblick-capability-helper
description: Guide for creating, reviewing, and composing Överblick capabilities
triggers:
  - create capability
  - new capability
  - review capability
  - add capability
  - capability structure
  - capability architecture
  - add bundle
---

# Överblick Capability Helper

You are helping a developer work with **capabilities** in the Överblick agent framework. Capabilities are composable behavioral building blocks that plugins wire together — like lego blocks. This is **composition over inheritance**: plugins don't subclass a monolithic base, they compose capabilities.

## Capability Lifecycle

```
__init__(ctx: CapabilityContext) → setup() → tick() [optional] → on_event() [optional] → teardown()
```

1. **`__init__(ctx)`** — Store context. No I/O.
2. **`setup()`** — Async. Initialize state. Raise `RuntimeError` if capability can't start.
3. **`tick()`** — Async. Called by owning plugin (not scheduler directly). Default is no-op.
4. **`on_event(event, **kwargs)`** — Async. React to events from the event bus. Default is no-op.
5. **`teardown()`** — Async. Cleanup resources. Default is no-op.
6. **`get_prompt_context()`** — Return string to inject into LLM prompts. Default returns `""`.

## Creating a New Capability

### Step 1: Gather Requirements
Ask the user:
- **Capability name** (lowercase, descriptive — e.g., `summarizer`, `mood_tracker`)
- **Bundle** (which bundle? existing: system, knowledge, social, engagement, conversation, content, speech, vision, communication, consulting, monitoring — or new. Note: psychology is DEPRECATED)
- **Does it need LLM?** (use `ctx.llm_pipeline` — NEVER use `ctx.llm_client` directly)
- **Does it need periodic work?** (override `tick()`)
- **Does it react to events?** (override `on_event()`)
- **Does it contribute to prompts?** (override `get_prompt_context()`)

### Step 2: Create the Capability

Create files:
```
overblick/capabilities/<bundle>/<name>.py     # Capability class
tests/capabilities/test_<name>.py             # Tests
```

If this is a new bundle, also create `overblick/capabilities/<bundle>/__init__.py`.

#### Capability Template

```python
"""
<Name>Capability — <brief description>.

<Details about what this capability does and how plugins use it.>
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class <Name>Capability(CapabilityBase):
    """<Brief description>."""

    name = "<registry_name>"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        # Initialize state

    async def setup(self) -> None:
        """Initialize capability state."""
        # Read config from self.ctx.config
        self._some_setting = self.ctx.config.get("some_setting", "default")
        logger.info("<Name>Capability initialized for %s", self.ctx.identity_name)

    async def tick(self) -> None:
        """Periodic work (called by owning plugin)."""
        pass

    async def on_event(self, event: str, **kwargs) -> None:
        """React to events."""
        if event == "post_created":
            pass

    def get_prompt_context(self) -> str:
        """Return context for LLM prompts."""
        return ""

    async def teardown(self) -> None:
        """Cleanup."""
        pass
```

### Step 3: Register in `capabilities/__init__.py`

Edit `overblick/capabilities/__init__.py`:

```python
# Add import
from overblick.capabilities.<bundle>.<name> import <Name>Capability

# Add to CAPABILITY_REGISTRY
CAPABILITY_REGISTRY: dict[str, type] = {
    # ... existing ...
    "<registry_name>": <Name>Capability,
}

# Add to CAPABILITY_BUNDLES (or create new bundle)
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    # ... existing ...
    "<bundle>": ["<registry_name>"],  # New bundle
    # OR add to existing: "psychology": [..., "<registry_name>"],
}

# Add to __all__
__all__ = [
    # ... existing ...
    "<Name>Capability",
]
```

### Step 4: Write Tests

```python
"""Tests for <Name>Capability."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from overblick.core.capability import CapabilityContext
from overblick.capabilities.<bundle>.<name> import <Name>Capability


def make_ctx(tmp_path, **overrides):
    """Create a test CapabilityContext."""
    defaults = {
        "identity_name": "test",
        "data_dir": tmp_path,
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class Test<Name>Capability:
    def test_creation(self, tmp_path):
        ctx = make_ctx(tmp_path)
        cap = <Name>Capability(ctx)
        assert cap.name == "<registry_name>"
        assert cap.enabled is True

    @pytest.mark.asyncio
    async def test_setup(self, tmp_path):
        ctx = make_ctx(tmp_path, config={"some_setting": "value"})
        cap = <Name>Capability(ctx)
        await cap.setup()
        # Assert setup worked

    @pytest.mark.asyncio
    async def test_teardown(self, tmp_path):
        ctx = make_ctx(tmp_path)
        cap = <Name>Capability(ctx)
        await cap.setup()
        await cap.teardown()  # Should not raise
```

### Step 5: Verify

```bash
# Run capability tests
./venv/bin/python3 -m pytest tests/capabilities/test_<name>.py -v

# Run all tests
./venv/bin/python3 -m pytest tests/ -v -m "not llm" -x
```

## Reviewing a Capability

When reviewing, check:

1. **CapabilityBase contract** — Extends `CapabilityBase`, has `name` attribute, `setup()` is async
2. **Context usage** — Uses `CapabilityContext`, not `PluginContext` directly
3. **Config access** — Reads config from `self.ctx.config`, not hardcoded
4. **Enabled check** — Respects `self.enabled` property
5. **Prompt context** — If it contributes to prompts, `get_prompt_context()` returns meaningful string
6. **Tests exist** — Coverage for setup, tick (if used), events (if used), teardown
7. **Registration** — Listed in `CAPABILITY_REGISTRY` and appropriate bundle

## Current Registry

### Capabilities

| Registry Name | Class | Bundle | Description |
|---------------|-------|--------|-------------|
| `dream_system` | `DreamCapability` | psychology | Morning dreams and housekeeping |
| `therapy_system` | `TherapyCapability` | psychology | Weekly psychological reflection |
| `emotional_state` | `EmotionalCapability` | psychology | Mood tracking from interactions |
| `safe_learning` | `LearningCapability` | knowledge | LLM-reviewed knowledge acquisition |
| `knowledge_loader` | `KnowledgeCapability` | knowledge | Load identity knowledge files |
| `openings` | `OpeningCapability` | social | Opening phrase selection |
| `analyzer` | `AnalyzerCapability` | engagement | Engagement analysis via DecisionEngine |
| `composer` | `ComposerCapability` | engagement | Response composition |
| `conversation_tracker` | `ConversationCapability` | conversation | Multi-turn conversation tracking |
| `summarizer` | `SummarizerCapability` | content | Text summarization via LLM |
| `stt` | `SpeechToTextCapability` | speech | Speech-to-text conversion |
| `tts` | `TextToSpeechCapability` | speech | Text-to-speech synthesis |
| `vision` | `VisionCapability` | vision | Image/video analysis |
| `boss_request` | `BossRequestCapability` | communication | Request approval from supervisor |
| `email` | `EmailCapability` | communication | Email sending capability |
| `gmail` | `GmailCapability` | communication | Gmail-specific email |
| `style_trainer` | `StyleTrainerCapability` | communication | Writing style learning (Deepseek via Gateway) |
| `telegram_notifier` | `TelegramNotifier` | communication | Telegram notifications |
| `host_inspection` | `HostInspectionCapability` | monitoring | System health monitoring |
| `system_clock` | `SystemClockCapability` | system | Time awareness for agents |
| `personality_consultant` | `PersonalityConsultantCapability` | consulting | Cross-identity consulting |

### Bundles

| Bundle | Capabilities | Notes |
|--------|-------------|-------|
| `system` | system_clock | Core capabilities injected into all agents |
| `psychology` | dream_system, therapy_system, emotional_state | **DEPRECATED** — use personality.yaml |
| `knowledge` | safe_learning, knowledge_loader | |
| `social` | openings | |
| `engagement` | analyzer, composer | |
| `conversation` | conversation_tracker | |
| `content` | summarizer | |
| `speech` | stt, tts | |
| `vision` | vision | |
| `communication` | boss_request, email, gmail, style_trainer, telegram_notifier | |
| `consulting` | personality_consultant | |
| `monitoring` | host_inspection | |

## CapabilityBase Quick Reference

| Method/Property | Type | Description |
|-----------------|------|-------------|
| `name` | `str` | Registry name (class attribute) |
| `ctx` | `CapabilityContext` | Context with identity, config, services |
| `enabled` | `bool` | Whether capability is active (default True) |
| `setup()` | async | Initialize — **abstract, must override** |
| `tick()` | async | Periodic work — optional |
| `on_event(event, **kw)` | async | React to events — optional |
| `teardown()` | async | Cleanup — optional |
| `get_prompt_context()` | `str` | Inject context into LLM prompts — optional |

## Key Files

| File | Purpose |
|------|---------|
| `overblick/core/capability.py` | `CapabilityBase`, `CapabilityContext`, `CapabilityRegistry` |
| `overblick/capabilities/__init__.py` | `CAPABILITY_REGISTRY`, `CAPABILITY_BUNDLES` |
| `overblick/capabilities/<bundle>/<name>.py` | Individual capability implementations |

## References

- `references/capability-architecture.md` — Full API details for CapabilityBase, Context, and Registry
- `references/capability-examples.md` — Real capability patterns (Summarizer, Conversation, Dream, Analyzer)
