# Plugin Code Template

Complete working plugin with ALL security patterns. Replace `<Name>` with PascalCase and `<name>` with lowercase throughout.

## overblick/plugins/\<name\>/plugin.py

```python
"""
<Name>Plugin — <brief description>.

<Detailed description of what this plugin does, how it interacts with
the external service, and its security properties.>

SECURITY: All LLM calls go through SafeLLMPipeline. All external content
is wrapped in boundary markers via wrap_external_content(). Secrets are
loaded via ctx.get_secret() and never logged.
"""

import logging
from typing import Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class <Name>Plugin(PluginBase):
    """
    <Brief description>.

    Lifecycle:
        setup() — Load secrets, create API client, audit setup
        tick()  — Poll external service, process data, generate responses
        teardown() — Close client connections, log summary
    """

    name = "<name>"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        # Initialize state (NO I/O here — do that in setup())
        self._client: Optional[...] = None  # Replace with actual client type
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load secrets, create clients, audit."""
        identity = self.ctx.identity
        logger.info("Setting up <Name>Plugin for identity: %s", identity.name)

        # --- Load secrets (fail-fast if missing) ---
        api_key = self.ctx.get_secret("<name>_api_key")
        if not api_key:
            raise RuntimeError(
                f"Missing <name>_api_key for identity {identity.name}"
            )

        # --- Load config from identity ---
        config = identity.raw_config
        # Example: self._max_items = config.get("<name>_max_items", 10)

        # --- Create API client ---
        # self._client = <Name>Client(api_key=api_key, ...)

        # --- Audit setup ---
        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )
        logger.info("<Name>Plugin setup complete for %s", identity.name)

    async def tick(self) -> None:
        """Main work cycle — called periodically by scheduler."""
        # --- Quiet hours check (MANDATORY before LLM work) ---
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        self._tick_count += 1

        try:
            # --- OBSERVE: Fetch data from external service ---
            # raw_items = await self._client.fetch_items()
            raw_items = []  # TODO: Replace with actual API call

            if not raw_items:
                return

            for item in raw_items:
                await self._process_item(item)

        except Exception as e:
            logger.error("<Name>Plugin tick error: %s", e, exc_info=True)

    async def _process_item(self, item: dict) -> None:
        """Process a single item from the external service.

        SECURITY: External content is wrapped in boundary markers.
        LLM calls go through the safe pipeline.
        """
        # --- Wrap ALL external content in boundary markers ---
        safe_content = wrap_external_content(
            str(item.get("content", ""))[:2000], "external_content"
        )
        safe_author = wrap_external_content(
            str(item.get("author", "unknown")), "author"
        )

        # --- Generate response via SafeLLMPipeline ---
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            logger.warning("No LLM pipeline available, skipping response generation")
            return

        messages = [
            {"role": "system", "content": "You are a helpful agent."},
            {"role": "user", "content": f"Respond to: {safe_content} by {safe_author}"},
        ]

        result = await pipeline.chat(
            messages=messages,
            temperature=self.ctx.identity.llm.temperature,
            max_tokens=self.ctx.identity.llm.max_tokens,
            audit_action="<name>_response",
        )

        # --- Handle blocked results ---
        if result.blocked:
            logger.warning(
                "<Name>Plugin response blocked at %s: %s",
                result.block_stage.value if result.block_stage else "unknown",
                result.block_reason,
            )
            return

        if not result.content:
            logger.warning("<Name>Plugin got empty response from pipeline")
            return

        # --- ACT: Send response back to external service ---
        # await self._client.send_response(item["id"], result.content)

        # --- Audit the action ---
        self.ctx.audit_log.log(
            action="<name>_response_sent",
            details={"item_id": item.get("id"), "content_length": len(result.content)},
        )

    async def teardown(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.close()
        logger.info("<Name>Plugin teardown complete")


# Connector alias (backward-compatible)
<Name>Connector = <Name>Plugin
```

## overblick/plugins/\<name\>/\_\_init\_\_.py

```python
from overblick.plugins.<name>.plugin import <Name>Plugin

__all__ = ["<Name>Plugin"]
```

## Key Patterns

### Secret Loading (fail-fast)
```python
api_key = self.ctx.get_secret("<name>_api_key")
if not api_key:
    raise RuntimeError(f"Missing <name>_api_key for identity {identity.name}")
```
- Always check in `setup()`, never in `tick()`
- Raise RuntimeError to prevent plugin from starting without credentials
- Never log the secret value — only log the key name

### External Content Wrapping
```python
from overblick.core.security.input_sanitizer import wrap_external_content

safe_text = wrap_external_content(raw_text[:2000], "source_label")
```
- Wrap ALL data from external APIs, webhooks, user messages
- Truncate to reasonable length before wrapping
- The label identifies the data source in security logs

### Pipeline Usage
```python
result = await self.ctx.llm_pipeline.chat(
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    temperature=0.7,
    max_tokens=500,
    audit_action="<name>_action_name",
)
if result.blocked:
    logger.warning("Blocked: %s", result.block_reason)
    return
# Use result.content
```
- ALWAYS use `self.ctx.llm_pipeline`, NEVER `self.ctx.llm_client`
- ALWAYS check `result.blocked` before using `result.content`
- Provide a descriptive `audit_action` for every call

### Quiet Hours Check
```python
if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
    return
```
- MUST be the first check in `tick()`
- Also check before any scheduled LLM work (heartbeats, summaries)

### Config from Identity
```python
config = self.ctx.identity.raw_config
max_items = config.get("<name>_max_items", 10)
threshold = config.get("<name>_threshold", 50.0)
```
- Read from `identity.raw_config` for plugin-specific settings
- Always provide sensible defaults
