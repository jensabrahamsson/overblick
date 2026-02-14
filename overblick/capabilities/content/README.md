# Content Capabilities

## Overview

The **content** bundle provides text processing and summarization capabilities for agent plugins. It enables agents to condense long-form content (posts, articles, conversation history) into concise summaries using LLM-powered analysis through the SafeLLMPipeline.

This bundle is used when agents need to digest information before responding, create executive summaries, or maintain compressed memory of past interactions.

## Capabilities

### SummarizerCapability

LLM-powered text summarization with configurable length and temperature. Uses the SafeLLMPipeline to ensure all security policies (input sanitization, preflight checks, rate limiting, output safety) are enforced during summarization.

**Registry name:** `summarizer`

## Methods

### SummarizerCapability

```python
async def summarize(self, text: str, max_length: int = 100) -> Optional[str]:
    """
    Summarize text using the LLM pipeline.

    Args:
        text: Text to summarize (truncated to 3000 chars).
        max_length: Target maximum word count for the summary.

    Returns:
        Summary string, or None if LLM is unavailable or fails.
    """
```

Configuration options (set in identity YAML under `capabilities.summarizer`):
- `temperature` (float, default 0.3) — LLM temperature for summarization
- `max_tokens` (int, default 500) — Maximum tokens in LLM response

## Plugin Integration

Plugins access the SummarizerCapability through the CapabilityContext:

```python
from overblick.core.capability import CapabilityContext, CapabilityRegistry

class MyPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load content bundle (includes summarizer)
        caps = registry.create_all(["content"], self.ctx)
        for cap in caps:
            await cap.setup()

        self.summarizer = caps[0]

    async def process_article(self, article: str):
        summary = await self.summarizer.summarize(article, max_length=150)
        if summary:
            logger.info("Article summary: %s", summary)
```

Or use the capability registry to load individual capabilities:

```python
summarizer = registry.create("summarizer", self.ctx, config={
    "temperature": 0.2,
    "max_tokens": 300,
})
await summarizer.setup()
```

## Configuration

Configure the content bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  summarizer:
    temperature: 0.3
    max_tokens: 500
```

Or load the entire bundle:

```yaml
capabilities:
  - content  # Expands to: summarizer
```

## Usage Examples

### Basic Summarization

```python
from overblick.capabilities.content import SummarizerCapability
from overblick.core.capability import CapabilityContext

# Create capability context
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    llm_client=ollama_client,
    llm_pipeline=safe_pipeline,
    config={"temperature": 0.3, "max_tokens": 500},
)

# Initialize summarizer
summarizer = SummarizerCapability(ctx)
await summarizer.setup()

# Summarize long text
article = """
Long article content here spanning multiple paragraphs...
"""
summary = await summarizer.summarize(article, max_length=100)
print(f"Summary: {summary}")
```

### Conversation History Compression

```python
# Compress conversation history before feeding to LLM
conversation_text = "\n".join([
    f"{msg['role']}: {msg['content']}" for msg in messages
])

compressed = await summarizer.summarize(
    conversation_text,
    max_length=200
)

# Use compressed version in context
context = f"Previous conversation summary: {compressed}"
```

### Security-Aware Summarization

The summarizer automatically uses SafeLLMPipeline when available:

```python
# Pipeline enforces full security chain:
# 1. Input sanitization
# 2. Preflight checks (skipped for summarization - internal content)
# 3. Rate limiting
# 4. LLM call
# 5. Output safety filtering
# 6. Audit logging

result = await summarizer.summarize(user_input, max_length=50)

# If blocked by security policy, returns None
if result is None:
    logger.warning("Summarization blocked or failed")
```

## Testing

Run content capability tests:

```bash
# Test summarizer (without LLM - mocked)
pytest tests/capabilities/test_summarizer.py -v

# Test with real LLM (requires Gateway + Ollama)
pytest tests/capabilities/test_summarizer.py -v -m llm
```

Example test pattern:

```python
import pytest
from overblick.capabilities.content import SummarizerCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_summarizer_basic(mock_llm_client):
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        llm_client=mock_llm_client,
        config={"temperature": 0.3},
    )

    summarizer = SummarizerCapability(ctx)
    await summarizer.setup()

    text = "Long text to summarize..."
    summary = await summarizer.summarize(text, max_length=50)

    assert summary is not None
    assert len(summary) > 0
```

## Architecture

### CapabilityBase

All capabilities inherit from `CapabilityBase`:

```python
class CapabilityBase(ABC):
    name: str = "unnamed"

    def __init__(self, ctx: CapabilityContext):
        self.ctx = ctx
        self._enabled = True

    @abstractmethod
    async def setup(self) -> None:
        """Initialize capability state."""

    async def tick(self) -> None:
        """Periodic work (optional)."""

    async def on_event(self, event: str, **kwargs) -> None:
        """React to events (optional)."""

    async def teardown(self) -> None:
        """Cleanup (optional)."""

    def get_prompt_context(self) -> str:
        """Inject context into LLM prompts (optional)."""
        return ""
```

### CapabilityContext

Capabilities receive a lightweight context — a subset of PluginContext containing only what they need:

```python
class CapabilityContext(BaseModel):
    identity_name: str
    data_dir: Path
    llm_client: Any = None
    event_bus: Any = None
    audit_log: Any = None
    quiet_hours_checker: Any = None
    identity: Any = None
    llm_pipeline: Any = None  # SafeLLMPipeline
    config: dict[str, Any] = {}  # Capability-specific config
```

This prevents capabilities from depending on plugin-specific state, keeping them reusable across different plugin types.

### Bundles

Bundles are named groups of capabilities that are commonly used together:

```python
CAPABILITY_BUNDLES = {
    "content": ["summarizer"],
    # ... other bundles
}
```

When a plugin requests the `content` bundle, the registry expands it to `["summarizer"]` and instantiates each capability with its own CapabilityContext.

## Related Bundles

- **knowledge** — Knowledge loading and safe learning
- **engagement** — Content analysis and response generation
- **conversation** — Multi-turn conversation tracking
