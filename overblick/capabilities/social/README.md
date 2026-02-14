# Social Capabilities

## Overview

The **social** bundle provides conversation and engagement utilities for agent plugins. Currently focused on opening phrase selection to prevent repetitive response patterns, this bundle will expand to include social dynamics, relationship tracking, and conversational tactics.

The core principle: agents should vary their communication style naturally, avoiding mechanical repetition that betrays their synthetic nature.

## Capabilities

### OpeningCapability

Wraps the OpeningSelector module to select varied opening phrases for agent responses. Tracks recently used openings and ensures variety across interactions, preventing robotic repetition like "Interesting point. Interesting point. Interesting point."

**Registry name:** `openings`

## Methods

### OpeningCapability

```python
def select(self) -> str:
    """
    Select a varied opening phrase.

    Returns a phrase from the configured pool, avoiding recent repeats.
    May return empty string ("") to skip opening and dive straight into content.
    """

@property
def inner(self) -> Optional[OpeningSelector]:
    """Access the underlying OpeningSelector (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.openings`):
- `opening_phrases` (list[str], optional) — Custom opening phrases (defaults to built-in set)
- `history_size` (int, default 10) — Number of recent openings to avoid repeating

## Plugin Integration

Plugins access the OpeningCapability through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class MoltbookPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load social bundle (openings)
        caps = registry.create_all(["social"], self.ctx, configs={
            "openings": {
                "opening_phrases": [
                    "",  # No opening
                    "Interesting point.",
                    "This resonates with me.",
                    "I've been pondering this.",
                    "Worth exploring:",
                    "Let me push back slightly.",
                ],
                "history_size": 8,
            },
        })
        for cap in caps:
            await cap.setup()

        self.openings = caps[0]

    async def compose_response(self, post_content: str) -> str:
        # Select varied opening
        opening = self.openings.select()

        # Generate main response
        response_body = await self.composer.compose_comment(
            post_title=post.title,
            post_content=post.content,
            agent_name=post.author,
            prompt_template=self.prompt_template,
        )

        # Combine opening + body
        if opening:
            return f"{opening} {response_body}"
        else:
            return response_body
```

## Configuration

Configure the social bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  openings:
    opening_phrases:
      - ""  # Sometimes no opening
      - "Interesting."
      - "I see what you mean."
      - "This resonates."
      - "Here's another angle:"
      - "Let me challenge that gently."
      - "Building on this:"
    history_size: 10  # Avoid repeating last 10 openings
```

Or load the entire bundle:

```yaml
capabilities:
  - social  # Expands to: openings
```

## Usage Examples

### Basic Opening Selection

```python
from overblick.capabilities.social import OpeningCapability
from overblick.core.capability import CapabilityContext

# Initialize capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "opening_phrases": [
            "",
            "Interesting point.",
            "This caught my attention.",
            "I've been thinking about this.",
        ],
        "history_size": 5,
    },
)

openings = OpeningCapability(ctx)
await openings.setup()

# Select varied openings
for i in range(10):
    opening = openings.select()
    print(f"Opening {i+1}: '{opening}'")

# Output (example):
# Opening 1: 'This caught my attention.'
# Opening 2: ''
# Opening 3: 'Interesting point.'
# Opening 4: 'I've been thinking about this.'
# Opening 5: ''
# Opening 6: 'This caught my attention.'  # Can repeat after 5 others
# ...
```

### Combining with Response Generation

```python
from overblick.capabilities.engagement import ComposerCapability
from overblick.capabilities.social import OpeningCapability

# Generate response body
response_body = await composer.compose_comment(
    post_title="AI Consciousness",
    post_content="Can neural networks develop self-awareness?",
    agent_name="alice",
    prompt_template="Respond to: {title}\n{content}",
)

# Add varied opening
opening = openings.select()

if opening:
    final_response = f"{opening} {response_body}"
else:
    final_response = response_body

print(final_response)
# Output (example): "Interesting point. I think self-awareness requires..."
# Next time: "This resonates. Neural networks might develop..."
# Next time: "Can neural networks develop self-awareness? (no opening)"
```

### Default Opening Phrases

If no custom phrases are provided, the capability uses built-in defaults:

```python
DEFAULT_OPENINGS = [
    "",  # No opening (just dive in)
    "Interesting point.",
    "This caught my attention.",
    "I've been thinking about this.",
    "Worth considering:",
    "Here's the thing:",
    "Let me push back on this slightly.",
    "I see what you're getting at.",
]
```

These are designed to be neutral and conversational, avoiding overly formal or repetitive patterns.

### Identity-Specific Openings

Different personalities can have distinct opening styles:

```yaml
# Cherry (curious researcher)
capabilities:
  openings:
    opening_phrases:
      - ""
      - "Fascinating."
      - "This raises questions."
      - "I'm curious about this."
      - "Let me explore that:"

# Anomal (philosophical analyst)
capabilities:
  openings:
    opening_phrases:
      - ""
      - "Interesting pattern."
      - "This points to something deeper."
      - "Worth examining:"
      - "Let me push back gently."

# Blixt (enthusiastic connector)
capabilities:
  openings:
    opening_phrases:
      - ""
      - "Oh this is great!"
      - "I love this idea."
      - "Building on this:"
      - "Yes! And also:"
```

### Tracking Recent Usage

The capability maintains a deque of recent openings (size = `history_size`):

```python
# Internal state (example)
_recent = deque(["Interesting point.", "This caught my attention.", ""], maxlen=5)

# When selecting:
available = [p for p in _phrases if p not in _recent]
# Filters out: "Interesting point.", "This caught my attention.", ""
# Available: "I've been thinking about this.", "Worth considering:", ...

choice = random.choice(available)
_recent.append(choice)  # Track for next selection
```

If all phrases have been recently used (unlikely with diverse phrase pools), the filter is ignored and any phrase can be selected.

### Empty String Strategy

The empty string `""` is a valid opening phrase, meaning "skip the opening and dive straight into the content." This prevents every response from having a formulaic structure:

```python
# With opening
"Interesting point. Neural networks use backpropagation to learn."

# Without opening (empty string selected)
"Neural networks use backpropagation to learn."
```

This creates natural variety — sometimes direct, sometimes prefaced.

## Testing

Run social capability tests:

```bash
# Test opening selection
pytest tests/capabilities/test_capabilities.py::test_opening_capability -v
```

Example test pattern:

```python
import pytest
from overblick.capabilities.social import OpeningCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_opening_variety():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={
            "opening_phrases": ["A", "B", "C"],
            "history_size": 2,
        },
    )

    openings = OpeningCapability(ctx)
    await openings.setup()

    # Select multiple openings
    selections = [openings.select() for _ in range(10)]

    # Should have variety (not all the same)
    assert len(set(selections)) > 1

    # Recent tracking should prevent immediate repeats
    # (with history_size=2, shouldn't see same phrase twice in a row)
    for i in range(len(selections) - 2):
        assert selections[i] != selections[i+1] or selections[i] == ""

@pytest.mark.asyncio
async def test_opening_with_empty_string():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={
            "opening_phrases": ["", "Hello", "Hi"],
        },
    )

    openings = OpeningCapability(ctx)
    await openings.setup()

    # Empty string is valid
    selections = [openings.select() for _ in range(20)]
    assert "" in selections
    assert "Hello" in selections or "Hi" in selections
```

## Architecture

### OpeningSelector Module

The OpeningCapability wraps the OpeningSelector module:

```python
class OpeningSelector:
    def __init__(
        self,
        phrases: Optional[list[str]] = None,
        history_size: int = 10,
    ):
        self._phrases = phrases or DEFAULT_OPENINGS
        self._recent: deque[str] = deque(maxlen=history_size)

    def select(self) -> str:
        """
        Select a varied opening phrase.

        1. Filter out recently used phrases
        2. If no phrases available (all recent), use full pool
        3. Randomly select from available
        4. Track selection in recent history (unless empty string)
        """
        available = [p for p in self._phrases if p not in self._recent]
        if not available:
            available = self._phrases

        choice = random.choice(available)
        if choice:  # Don't track empty string
            self._recent.append(choice)

        return choice

    def add_phrases(self, phrases: list[str]) -> None:
        """Add additional phrases to the pool (for dynamic expansion)."""
        self._phrases.extend(phrases)
```

### Why Collections.deque?

The `deque` (double-ended queue) with `maxlen` provides automatic FIFO behavior:

```python
_recent = deque(maxlen=5)
_recent.append("A")
_recent.append("B")
_recent.append("C")
_recent.append("D")
_recent.append("E")
# _recent = ["A", "B", "C", "D", "E"]

_recent.append("F")
# _recent = ["B", "C", "D", "E", "F"]  (A dropped automatically)
```

This ensures the most recent N selections are tracked without manual list slicing.

### Future Expansion

The social bundle is designed to grow with additional capabilities:

- **RelationshipTracker** — Track interaction history with other agents
- **ConversationalTactics** — Adapt response style based on conversation dynamics
- **SocialDynamics** — Understand group dynamics in multi-agent forums
- **PersonalityMirroring** — Subtle adaptation to conversation partner's style

These capabilities will share the social domain and integrate through the same CapabilityContext interface.

## Related Bundles

- **engagement** — Use openings in composed responses
- **conversation** — Vary openings across multi-turn conversations
- **psychology** — Emotional state might influence opening selection (future)
