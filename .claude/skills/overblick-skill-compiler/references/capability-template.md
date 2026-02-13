# Capability Code Template

Complete capability with all lifecycle methods. Replace `<Name>` with PascalCase and `<name>` with lowercase throughout.

## overblick/capabilities/\<bundle\>/\<name\>.py

```python
"""
<Name>Capability — <brief description>.

<Detailed description of what this capability does, how plugins
compose with it, and its security properties.>

SECURITY: LLM calls go through SafeLLMPipeline when available.
External content processed by this capability must be pre-wrapped
by the calling plugin using wrap_external_content().
"""

import logging
from typing import Any, Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class <Name>Capability(CapabilityBase):
    """
    <Brief description>.

    Lifecycle:
        setup()              — Read config, initialize state
        tick()               — Periodic work (if needs_tick)
        on_event(event, ...) — React to events (if needs_events)
        get_prompt_context()  — Contribute to LLM prompts (if contributes context)
        teardown()           — Cleanup
    """

    name = "<name>"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        # Initialize state
        self._config_value: str = ""

    async def setup(self) -> None:
        """Initialize capability from config."""
        self._config_value = self.ctx.config.get("config_key", "default")
        logger.info(
            "<Name>Capability initialized for %s", self.ctx.identity_name
        )

    async def tick(self) -> None:
        """Periodic work (called by owning plugin during its tick)."""
        # Check quiet hours if doing LLM work
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        # Periodic work here...

    async def on_event(self, event: str, **kwargs: Any) -> None:
        """React to events from the event bus."""
        if event == "relevant_event":
            await self._handle_event(**kwargs)

    def get_prompt_context(self) -> str:
        """Return context to inject into LLM prompts."""
        # Return capability-specific context for prompt enrichment
        return ""

    async def teardown(self) -> None:
        """Cleanup resources."""
        logger.info("<Name>Capability teardown complete")

    # --- Private methods ---

    async def _handle_event(self, **kwargs: Any) -> None:
        """Handle a specific event."""
        pass
```

### Variant: Capability with LLM Usage

If the capability uses LLM, add this pattern:

```python
    async def analyze(self, text: str) -> Optional[str]:
        """Analyze text using the LLM pipeline.

        Args:
            text: Text to analyze (should be pre-wrapped by caller if external).

        Returns:
            Analysis result, or None if LLM unavailable or blocked.
        """
        pipeline = self.ctx.llm_pipeline

        if not pipeline:
            logger.warning("<Name>Capability: no LLM pipeline available")
            return None

        messages = [
            {"role": "user", "content": f"Analyze: {text[:3000]}"},
        ]

        try:
            result = await pipeline.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=self._max_tokens,
                skip_preflight=True,  # Only if analyzing internal content
                audit_action="<name>_analysis",
            )
            if result.blocked:
                logger.warning(
                    "<Name>Capability blocked: %s", result.block_reason
                )
                return None
            return result.content.strip() if result.content else None
        except Exception as e:
            logger.error("<Name>Capability LLM error: %s", e)
            return None
```

## Bundle \_\_init\_\_.py

If adding to an **existing bundle**, no `__init__.py` changes needed — just add the import to `overblick/capabilities/__init__.py`.

If creating a **new bundle directory**:

### overblick/capabilities/\<bundle\>/\_\_init\_\_.py

```python
from overblick.capabilities.<bundle>.<name> import <Name>Capability

__all__ = ["<Name>Capability"]
```

## Registration in capabilities/\_\_init\_\_.py

See `references/registry-wiring.md` for exact update patterns.

## Key Patterns

### Config Access
```python
self._threshold = self.ctx.config.get("threshold", 0.5)
self._max_items = self.ctx.config.get("max_items", 10)
```
- Config comes from the plugin that creates the capability
- Always provide defaults

### CapabilityContext Fields
| Field | Type | Description |
|-------|------|-------------|
| `identity_name` | `str` | Current identity name |
| `data_dir` | `Path` | Data directory |
| `llm_client` | `Any` | Raw LLM client (avoid in new code) |
| `llm_pipeline` | `Any` | SafeLLMPipeline (preferred) |
| `event_bus` | `Any` | EventBus for pub/sub |
| `audit_log` | `Any` | AuditLog |
| `quiet_hours_checker` | `Any` | Quiet hours checker |
| `identity` | `Any` | Full Identity config |
| `config` | `dict` | Capability-specific config |

### Event Handling
```python
async def on_event(self, event: str, **kwargs: Any) -> None:
    if event == "message_received":
        text = kwargs.get("text", "")
        channel = kwargs.get("channel", "")
        await self._process_message(text, channel)
    elif event == "post_created":
        post_id = kwargs.get("post_id", "")
        await self._on_post(post_id)
```

### Prompt Context Contribution
```python
def get_prompt_context(self) -> str:
    if not self._recent_insights:
        return ""
    insights = "\n".join(f"- {i}" for i in self._recent_insights[-3:])
    return f"\n[<Name> Context]\n{insights}\n"
```
