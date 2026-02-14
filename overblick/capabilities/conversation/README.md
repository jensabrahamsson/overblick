# Conversation Capabilities

## Overview

The **conversation** bundle provides multi-turn conversation tracking and management for agent plugins. It maintains per-conversation message history with automatic stale conversation cleanup, enabling agents to have contextual, coherent conversations across multiple turns.

Extracted from the TelegramPlugin's inline ConversationContext pattern and generalized into a reusable capability usable by any connector (Telegram, Discord, Matrix, Slack, etc.).

## Capabilities

### ConversationCapability

Manages conversation history with configurable max history length and stale timeout. Tracks user and assistant messages separately, provides full message list construction, and automatically cleans up inactive conversations.

**Registry name:** `conversation_tracker`

## Methods

### ConversationCapability

```python
def get_or_create(self, conversation_id: str) -> ConversationEntry:
    """Get or create a conversation entry."""

def add_user_message(self, conversation_id: str, text: str) -> None:
    """Add a user message to a conversation."""

def add_assistant_message(self, conversation_id: str, text: str) -> None:
    """Add an assistant message to a conversation."""

def get_messages(self, conversation_id: str, system_prompt: str = "") -> list[dict[str, str]]:
    """
    Get message history for a conversation.

    Returns a list of message dicts with 'role' and 'content' keys.
    If system_prompt is provided, it's prepended to the message list.
    """

def reset(self, conversation_id: str) -> None:
    """Reset a conversation's history."""

def cleanup_stale(self) -> int:
    """Remove stale conversations. Returns count of removed conversations."""

@property
def active_count(self) -> int:
    """Number of active conversations."""
```

### ConversationEntry Model

```python
class ConversationEntry(BaseModel):
    conversation_id: str
    messages: list[dict[str, str]] = []
    last_active: float  # Unix timestamp
    max_history: int = 10

    def add_user_message(self, text: str) -> None
    def add_assistant_message(self, text: str) -> None
    def get_messages(self, system_prompt: str = "") -> list[dict[str, str]]

    @property
    def is_stale(self) -> bool
        """Conversation is stale if inactive for > 1 hour (default)."""
```

Configuration options (set in identity YAML under `capabilities.conversation_tracker`):
- `max_history` (int, default 10) — Maximum number of user/assistant turns to retain
- `stale_seconds` (int, default 3600) — Seconds of inactivity before conversation is considered stale

## Plugin Integration

Plugins access the ConversationCapability through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class TelegramPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load conversation bundle
        caps = registry.create_all(["conversation"], self.ctx)
        for cap in caps:
            await cap.setup()

        self.conversation = caps[0]

    async def handle_message(self, chat_id: str, user_message: str):
        # Add user message to conversation
        self.conversation.add_user_message(chat_id, user_message)

        # Get full conversation history
        messages = self.conversation.get_messages(
            chat_id,
            system_prompt=self.system_prompt
        )

        # Send to LLM
        response = await self.llm_client.chat(messages=messages)

        # Record assistant's response
        self.conversation.add_assistant_message(chat_id, response["content"])

        return response["content"]

    async def tick(self) -> None:
        # Clean up stale conversations periodically
        removed = self.conversation.cleanup_stale()
        if removed:
            logger.info("Cleaned up %d stale conversations", removed)
```

## Configuration

Configure the conversation bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  conversation_tracker:
    max_history: 15        # Keep last 15 turns
    stale_seconds: 7200    # 2 hours timeout
```

Or load the entire bundle:

```yaml
capabilities:
  - conversation  # Expands to: conversation_tracker
```

## Usage Examples

### Basic Multi-Turn Conversation

```python
from overblick.capabilities.conversation import ConversationCapability
from overblick.core.capability import CapabilityContext

# Initialize capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    llm_client=ollama_client,
    config={"max_history": 10, "stale_seconds": 3600},
)

conversation = ConversationCapability(ctx)
await conversation.setup()

# User sends first message
conversation.add_user_message("user123", "What's the weather?")

# Get history for LLM
messages = conversation.get_messages("user123", system_prompt="You are a helpful assistant.")
# Returns: [
#   {"role": "system", "content": "You are a helpful assistant."},
#   {"role": "user", "content": "What's the weather?"}
# ]

# Agent responds
conversation.add_assistant_message("user123", "I don't have weather data, but...")

# User continues conversation
conversation.add_user_message("user123", "How about stocks?")

# Now messages has full history
messages = conversation.get_messages("user123")
# Returns: [
#   {"role": "user", "content": "What's the weather?"},
#   {"role": "assistant", "content": "I don't have weather data, but..."},
#   {"role": "user", "content": "How about stocks?"}
# ]
```

### Conversation Reset

```python
# User types /reset or similar command
conversation.reset("user123")

# Next message starts fresh
conversation.add_user_message("user123", "Hello again!")
messages = conversation.get_messages("user123")
# Returns only: [{"role": "user", "content": "Hello again!"}]
```

### Periodic Cleanup with tick()

The capability implements the `tick()` lifecycle method for periodic cleanup:

```python
# Called by plugin's tick() method
async def tick(self) -> None:
    await self.conversation.tick()  # Automatically cleans up stale conversations
```

Or manually trigger cleanup:

```python
# Clean up conversations inactive for > stale_seconds
removed = conversation.cleanup_stale()
logger.info("Removed %d stale conversations", removed)
```

### Multiple Conversation Tracking

```python
# Different users/channels in parallel
conversation.add_user_message("alice", "Tell me about AI")
conversation.add_user_message("bob", "What's 2+2?")
conversation.add_user_message("channel-general", "Anyone here?")

# Each conversation maintains independent history
alice_msgs = conversation.get_messages("alice")
bob_msgs = conversation.get_messages("bob")
channel_msgs = conversation.get_messages("channel-general")

# Check active conversation count
print(f"Active conversations: {conversation.active_count}")  # Output: 3
```

### Integration with Capabilities

Conversation tracking pairs well with other capabilities:

```python
# Get conversation history
messages = conversation.get_messages(chat_id)

# Summarize with SummarizerCapability
full_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
summary = await summarizer.summarize(full_text, max_length=100)

# Use summary in context-aware response
context = f"Conversation so far: {summary}"
```

## Testing

Run conversation capability tests:

```bash
# Test conversation tracking
pytest tests/capabilities/test_conversation.py -v
```

Example test pattern:

```python
import pytest
from overblick.capabilities.conversation import ConversationCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_conversation_tracking():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={"max_history": 5},
    )

    conv = ConversationCapability(ctx)
    await conv.setup()

    # Add messages
    conv.add_user_message("chat1", "Hello")
    conv.add_assistant_message("chat1", "Hi there!")
    conv.add_user_message("chat1", "How are you?")

    # Get messages
    messages = conv.get_messages("chat1")
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"

@pytest.mark.asyncio
async def test_stale_cleanup():
    conv = ConversationCapability(ctx)
    await conv.setup()

    conv.add_user_message("old_chat", "Hi")

    # Manually mark as stale
    entry = conv.get_or_create("old_chat")
    entry.last_active = time.time() - 7200  # 2 hours ago

    removed = conv.cleanup_stale()
    assert removed == 1
    assert conv.active_count == 0
```

## Architecture

### Lifecycle Integration

ConversationCapability implements the full capability lifecycle:

```python
async def setup(self) -> None:
    # Initialize from config
    self._max_history = self.ctx.config.get("max_history", 10)
    self._stale_seconds = self.ctx.config.get("stale_seconds", 3600)

async def tick(self) -> None:
    # Periodic cleanup
    self.cleanup_stale()

async def teardown(self) -> None:
    # Optional cleanup (currently no-op)
    pass
```

### Message Format

All messages follow OpenAI's chat format:

```python
{
    "role": "system" | "user" | "assistant",
    "content": str
}
```

This makes conversation history directly compatible with LLM APIs (OpenAI, Anthropic, Ollama, etc.).

### History Truncation

When conversation exceeds `max_history * 2` messages, the capability automatically truncates to keep the most recent messages:

```python
def add_user_message(self, text: str) -> None:
    self.messages.append({"role": "user", "content": text})
    if len(self.messages) > self.max_history * 2:
        self.messages = self.messages[-self.max_history * 2:]
    self.last_active = time.time()
```

### Stale Detection

Conversations are considered stale after `stale_seconds` of inactivity:

```python
@property
def is_stale(self) -> bool:
    return (time.time() - self.last_active) > self._stale_seconds
```

The `tick()` method runs cleanup automatically, removing stale conversations to prevent memory leaks.

## Related Bundles

- **content** — Summarize conversation history
- **engagement** — Generate responses based on conversation context
- **social** — Select conversation opening phrases
