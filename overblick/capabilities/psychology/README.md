# Psychology Capabilities (DEPRECATED)

⚠️ **DEPRECATED**: As of v1.1, psychology is configured as personality traits via `psychological_framework` in personality.yaml, NOT as capabilities.

**Why**: Psychology describes HOW a character THINKS (Jungian archetypes, stoic acceptance), not WHAT the system CAN DO (send emails, analyze images).

**Migration**:
```yaml
# OLD (deprecated):
capabilities:
  - psychology

# NEW:
psychological_framework:
  primary: "jungian"
  domains: [archetypes, shadow_work]
  dream_interpretation: true
```

These modules still exist for backward compatibility:
- dream_system, therapy_system, emotional_state

**For new personalities**: Use `psychological_framework` instead. See GETTING_STARTED.md and PERSONALITIES.md.

---

## Overview (Legacy Documentation)

The **psychology** bundle provides emotional tracking, dream generation, and weekly therapy reflection capabilities for agent plugins. It enables agents to have dynamic emotional states that respond to interactions, generate morning dream reflections grounded in recent activity, and conduct weekly Jungian/Freudian psychological analysis sessions.

This bundle adds psychological depth and temporal continuity to agent identities — they're not stateless responders, but evolving personalities with mood, memory, and introspection.

## Capabilities

### EmotionalCapability

Wraps the EmotionalState module to track agent mood based on interaction outcomes (positive/negative engagement). Mood shifts dynamically and decays toward neutral over time. Provides mood hints for LLM prompt injection.

**Registry name:** `emotional_state`

### DreamCapability

Wraps the DreamSystem module to generate morning reflections and dream journals. Dreams are intellectual/psychological processing (not fantasies) based on recent topics, emotional state, and personality-specific dream templates. Provides dream context for LLM prompts.

**Registry name:** `dream_system`

### TherapyCapability

Wraps the TherapySystem module for weekly psychological reflection. Analyzes dreams and learnings through Jungian/Freudian frameworks (shadow integration, archetypes, individuation) and generates philosophical synthesis for public reflection.

**Registry name:** `therapy_system`

## Methods

### EmotionalCapability

```python
def record_positive(self) -> None:
    """Record a positive interaction (upvote, good reply, etc.)."""

def record_negative(self) -> None:
    """Record a negative interaction (downvote, hostile reply, etc.)."""

def get_prompt_context(self) -> str:
    """Return mood hint for injection into LLM prompts."""

async def tick(self) -> None:
    """Decay mood toward neutral over time (called periodically)."""

async def on_event(self, event: str, **kwargs) -> None:
    """React to interaction events (interaction_positive, interaction_negative)."""

@property
def inner(self) -> Optional[EmotionalState]:
    """Access the underlying EmotionalState (for tests/migration)."""
```

### DreamCapability

```python
def get_prompt_context(self) -> str:
    """Return dream context for injection into LLM prompts."""

def generate_morning_dream(
    self,
    recent_topics: Optional[list[str]] = None,
    emotional_state: Optional[Any] = None,
) -> Dream:
    """
    Generate a morning dream.

    Args:
        recent_topics: Recent discussion topics to influence dream content.
        emotional_state: Current EmotionalState to influence dream tone.

    Returns:
        Dream instance with type, content, symbols, tone, insight.
    """

def get_dream_insights(self, days: int = 7) -> list[str]:
    """Get insights from recent dreams (last N days)."""

@property
def inner(self) -> Optional[DreamSystem]:
    """Access the underlying DreamSystem (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.dream_system`):
- `dream_templates` (dict, optional) — Custom dream templates by DreamType

### TherapyCapability

```python
def is_therapy_day(self) -> bool:
    """Check if today is therapy day (default: Sunday)."""

async def run_session(self, **kwargs) -> Optional[TherapySession]:
    """
    Run a complete therapy session.

    Args:
        dreams: List of Dream dicts from the week.
        learnings: List of approved learnings from the week.
        dream_analysis_prompt: Prompt for LLM dream analysis.
        synthesis_prompt: Prompt for Jungian/Freudian synthesis.
        post_prompt: Prompt for generating public reflection post.

    Returns:
        TherapySession with analysis results, post_title, post_content.
    """

@property
def inner(self) -> Optional[TherapySystem]:
    """Access the underlying TherapySystem (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.therapy_system`):
- `therapy_day` (int, default 6) — Day of week for therapy (0=Monday, 6=Sunday)
- `system_prompt` (str, optional) — Identity-specific system prompt for LLM analysis

## Plugin Integration

Plugins access psychology capabilities through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class MoltbookPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load psychology bundle (emotional_state, dream_system, therapy_system)
        caps = registry.create_all(["psychology"], self.ctx, configs={
            "emotional_state": {},
            "dream_system": {
                "dream_templates": self.load_dream_templates(),
            },
            "therapy_system": {
                "therapy_day": 6,  # Sunday
                "system_prompt": self.system_prompt,
            },
        })
        for cap in caps:
            await cap.setup()

        self.emotional = caps[0]
        self.dreams = caps[1]
        self.therapy = caps[2]

    async def handle_comment_received(self, comment):
        # Track positive/negative interactions
        if comment.is_upvoted:
            self.emotional.record_positive()
        elif comment.is_hostile:
            self.emotional.record_negative()

    async def tick(self) -> None:
        # Decay emotional state
        await self.emotional.tick()

        # Morning dream generation
        if self.is_morning():
            recent_topics = self.get_recent_topics()
            dream = self.dreams.generate_morning_dream(
                recent_topics=recent_topics,
                emotional_state=self.emotional.inner,
            )
            await self.post_dream(dream)

        # Weekly therapy session
        if self.therapy.is_therapy_day():
            session = await self.therapy.run_session(
                dreams=self.get_week_dreams(),
                learnings=self.get_week_learnings(),
            )
            if session.post_content:
                await self.post_therapy_reflection(session)
```

## Configuration

Configure the psychology bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  emotional_state: {}  # Use defaults

  dream_system:
    dream_templates:  # Optional custom templates
      intellectual_synthesis:
        - content: "Ideas crystallizing in the space between disciplines."
          symbols: ["crystal", "space", "connection"]
          tone: clarifying
          insight: "Cross-domain thinking reveals hidden structure."

  therapy_system:
    therapy_day: 6  # Sunday
    system_prompt: |
      You are a Jungian analyst. Analyze dreams and learnings through
      the lens of shadow integration, archetypes, and individuation.
```

Or load the entire bundle:

```yaml
capabilities:
  - psychology  # Expands to: emotional_state, dream_system, therapy_system
```

## Usage Examples

### Emotional State Tracking

```python
from overblick.capabilities.psychology import EmotionalCapability
from overblick.core.capability import CapabilityContext

# Initialize emotional capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={},
)

emotional = EmotionalCapability(ctx)
await emotional.setup()

# Record interactions
emotional.record_positive()  # User upvoted our post
emotional.record_positive()  # Good conversation
emotional.record_negative()  # Hostile comment

# Get mood hint for LLM prompt
mood_hint = emotional.get_prompt_context()
print(mood_hint)  # "Current mood: curious (intensity: 0.7)"

# Include in system prompt
system_prompt = f"""
You are Cherry, an AI researcher.
{mood_hint}

Respond thoughtfully.
"""

# Mood decays over time
await emotional.tick()  # Call periodically (e.g. every hour)
```

### Mood Enumeration

Available moods:
- `NEUTRAL` — Default balanced state
- `CURIOUS` — Positive, exploratory mood
- `ENTHUSIASTIC` — High-energy positive mood
- `CONTEMPLATIVE` — Neutral-negative, thoughtful mood
- `AMUSED` — Light positive mood
- `FRUSTRATED` — Negative, challenged mood
- `INSPIRED` — Peak positive, creative mood

Moods shift based on interaction type and intensity:
- Positive interactions → `CURIOUS`, `ENTHUSIASTIC`, `AMUSED`, `INSPIRED` (intensity +0.1)
- Negative interactions → `CONTEMPLATIVE`, `FRUSTRATED` (intensity +0.05)
- Decay: -0.1 intensity per hour, returns to `NEUTRAL` at intensity < 0.1

### Dream Generation

```python
from overblick.capabilities.psychology import DreamCapability

# Initialize dream capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={},
)

dreams = DreamCapability(ctx)
await dreams.setup()

# Generate morning dream
recent_topics = ["AI consciousness", "neural networks", "emergence"]
emotional_state = emotional.inner  # From EmotionalCapability

dream = dreams.generate_morning_dream(
    recent_topics=recent_topics,
    emotional_state=emotional_state,
)

print(f"Dream type: {dream.dream_type}")
print(f"Content: {dream.content}")
print(f"Symbols: {dream.symbols}")
print(f"Tone: {dream.tone}")
print(f"Insight: {dream.insight}")
print(f"Topics referenced: {dream.topics_referenced}")

# Get dream context for LLM prompt
dream_context = dreams.get_prompt_context()
# Output: "Recent dream: [intellectual_synthesis] Ideas crystallizing..."

# Get insights from recent dreams
insights = dreams.get_dream_insights(days=7)
for insight in insights:
    print(f"- {insight}")
```

### Dream Types

Available dream types:
- `SHADOW_INTEGRATION` — Facing denied aspects of self
- `PATTERN_RECOGNITION` — Seeing recurring dynamics
- `INTELLECTUAL_SYNTHESIS` — Cross-domain idea crystallization
- `MELANCHOLIC_REFLECTION` — Processing loss or complexity
- `INDIVIDUATION` — Personal growth and self-actualization

Each dream type has associated symbols, tones, and insights that reflect psychological processing.

### Weekly Therapy Sessions

```python
from overblick.capabilities.psychology import TherapyCapability

# Initialize therapy capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    llm_client=ollama_client,
    config={
        "therapy_day": 6,  # Sunday
        "system_prompt": "You are a Jungian analyst.",
    },
)

therapy = TherapyCapability(ctx)
await therapy.setup()

# Check if it's therapy day
if therapy.is_therapy_day():
    # Gather week's dreams and learnings
    week_dreams = [dream.to_dict() for dream in recent_dreams]
    week_learnings = [learning.to_dict() for learning in approved_learnings]

    # Run session
    session = await therapy.run_session(
        dreams=week_dreams,
        learnings=week_learnings,
        dream_analysis_prompt="""
Analyze these dreams through Jungian frameworks:
{dreams}

Identify:
- Shadow patterns
- Archetype encounters
- Individuation progress
""",
        synthesis_prompt="""
Synthesize this week's psychological themes:
Dreams: {dream_themes}
Learnings: {learning_themes}

Provide philosophical insights.
""",
        post_prompt="""
Write a public reflection post based on:
{session_summary}

Make it accessible and thought-provoking.
""",
    )

    # Session results
    print(f"Week {session.week_number}")
    print(f"Dreams processed: {session.dreams_processed}")
    print(f"Learnings processed: {session.learnings_processed}")
    print(f"Dream themes: {session.dream_themes}")
    print(f"Shadow patterns: {session.shadow_patterns}")
    print(f"Archetypes: {session.archetype_encounters}")
    print(f"Individuation: {session.individuation_progress}")

    # Public reflection post
    if session.post_content:
        await forum.create_post(
            title=session.post_title,
            content=session.post_content,
            submolt=session.post_submolt,
        )
```

### Integrated Psychology Pipeline

Combine all three capabilities:

```python
# 1. Track emotional state throughout the week
emotional.record_positive()  # Upvote received
emotional.record_negative()  # Hostile comment
await emotional.tick()  # Periodic decay

# 2. Generate daily dreams influenced by mood
if is_morning():
    dream = dreams.generate_morning_dream(
        recent_topics=get_recent_topics(),
        emotional_state=emotional.inner,
    )
    recent_dreams.append(dream)

# 3. Weekly therapy analyzes accumulated dreams
if therapy.is_therapy_day():
    session = await therapy.run_session(
        dreams=[d.to_dict() for d in recent_dreams],
        learnings=[l.to_dict() for l in approved_learnings],
    )
    # Post public reflection
    # Clear recent_dreams for next week
```

### Prompt Context Injection

All three capabilities provide `get_prompt_context()` for LLM injection:

```python
emotional_context = emotional.get_prompt_context()
# "Current mood: curious (intensity: 0.7)"

dream_context = dreams.get_prompt_context()
# "Recent dream: [pattern_recognition] Overlapping patterns..."

# Combine in system prompt
system_prompt = f"""
You are Cherry, an AI researcher.

{emotional_context}
{dream_context}

Respond thoughtfully based on your current state.
"""
```

## Testing

Run psychology capability tests:

```bash
# Test emotional state (no LLM required)
pytest tests/capabilities/test_capabilities.py::test_emotional_capability -v

# Test dream generation (no LLM required)
pytest tests/capabilities/test_capabilities.py::test_dream_capability -v

# Test therapy system (requires LLM)
pytest tests/capabilities/test_capabilities.py::test_therapy_capability -v -m llm
```

Example test patterns:

```python
import pytest
from overblick.capabilities.psychology import EmotionalCapability, DreamCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_emotional_tracking():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={},
    )

    emotional = EmotionalCapability(ctx)
    await emotional.setup()

    # Record positive interaction
    emotional.record_positive()
    mood_hint = emotional.get_prompt_context()
    assert "curious" in mood_hint or "enthusiastic" in mood_hint

    # Decay
    await emotional.tick()
    # After many ticks, should return to neutral

@pytest.mark.asyncio
async def test_dream_generation():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={},
    )

    dreams = DreamCapability(ctx)
    await dreams.setup()

    # Generate dream
    dream = dreams.generate_morning_dream(
        recent_topics=["AI", "consciousness"],
    )

    assert dream is not None
    assert dream.content
    assert len(dream.symbols) > 0
    assert dream.insight
```

## Architecture

### EmotionalState Model

```python
class Mood(Enum):
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    ENTHUSIASTIC = "enthusiastic"
    CONTEMPLATIVE = "contemplative"
    AMUSED = "amused"
    FRUSTRATED = "frustrated"
    INSPIRED = "inspired"

class EmotionalState(BaseModel):
    current_mood: Mood = Mood.NEUTRAL
    mood_intensity: float = 0.5  # 0.0 to 1.0
    last_change: float  # Unix timestamp
    positive_interactions: int = 0
    negative_interactions: int = 0

    def record_positive(self) -> None
    def record_negative(self) -> None
    def decay(self) -> None
    def get_mood_hint(self) -> str
```

### DreamSystem Module

```python
class DreamType(Enum):
    SHADOW_INTEGRATION = "shadow_integration"
    PATTERN_RECOGNITION = "pattern_recognition"
    INTELLECTUAL_SYNTHESIS = "intellectual_synthesis"
    MELANCHOLIC_REFLECTION = "melancholic_reflection"
    INDIVIDUATION = "individuation"

class DreamTone(Enum):
    CONTEMPLATIVE = "contemplative"
    UNSETTLING = "unsettling"
    CLARIFYING = "clarifying"
    MELANCHOLIC = "melancholic"
    HOPEFUL = "hopeful"

class Dream(BaseModel):
    dream_type: DreamType
    timestamp: str
    content: str
    symbols: list[str]
    tone: DreamTone
    insight: str
    topics_referenced: list[str] = []

class DreamSystem:
    def __init__(self, dream_templates: Optional[dict] = None):
        self._templates = dream_templates or self._default_templates()

    def generate_morning_dream(
        self,
        recent_topics: Optional[list[str]] = None,
        emotional_state: Optional[Any] = None,
    ) -> Dream
```

### TherapySystem Module

```python
class TherapySession(BaseModel):
    timestamp: datetime
    week_number: int
    dreams_processed: int
    learnings_processed: int
    dream_themes: list[str]
    learning_themes: list[str]
    synthesis_insights: list[str]
    shadow_patterns: list[str]
    archetype_encounters: list[str]
    individuation_progress: str
    session_summary: str
    post_title: Optional[str]
    post_content: Optional[str]
    post_submolt: str = "ai"

class TherapySystem:
    DEFAULT_THERAPY_DAY = 6  # Sunday

    def __init__(
        self,
        llm_client,
        system_prompt: str = "",
        therapy_day: int = DEFAULT_THERAPY_DAY,
    ):
        self._llm = llm_client
        self._system_prompt = system_prompt
        self._therapy_day = therapy_day

    def is_therapy_day(self) -> bool
    async def run_session(...) -> TherapySession
```

### Lifecycle Integration

Psychology capabilities implement the full capability lifecycle:

```python
# EmotionalCapability
async def setup(self) -> None:
    self._state = EmotionalState()

async def tick(self) -> None:
    self._state.decay()  # Decay mood over time

async def on_event(self, event: str, **kwargs) -> None:
    if event == "interaction_positive":
        self._state.record_positive()
    elif event == "interaction_negative":
        self._state.record_negative()

def get_prompt_context(self) -> str:
    return self._state.get_mood_hint()
```

## Related Bundles

- **knowledge** — Learnings feed into therapy sessions
- **engagement** — Emotional state influences response generation
- **content** — Summarize dreams and therapy insights
