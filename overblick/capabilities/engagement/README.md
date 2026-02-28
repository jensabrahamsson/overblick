# Engagement Capabilities

## Overview

The **engagement** bundle provides content analysis, decision-making, and LLM-powered response generation for agent plugins. It enables agents to intelligently evaluate posts and comments for engagement worthiness, then generate contextual responses using identity-driven prompts and the SafeLLMPipeline.

This bundle is the core of social interaction — it decides what to engage with and how to respond.

## Capabilities

### AnalyzerCapability

Wraps the DecisionEngine module to evaluate posts and comments for engagement worthiness. Uses identity-driven interest keywords, configurable thresholds, and scoring rules to decide whether to comment, upvote, or skip content.

**Registry name:** `analyzer`

### ComposerCapability

Wraps the ResponseGenerator module to generate LLM-powered responses (comments, replies, heartbeat posts). Uses the SafeLLMPipeline for full security enforcement (input sanitization, preflight checks, rate limiting, output safety, audit logging).

**Registry name:** `composer`

## Methods

### AnalyzerCapability

```python
def evaluate(
    self,
    title: str,
    content: str,
    agent_name: str,
    submolt: str = "",
) -> EngagementDecision:
    """
    Evaluate whether to engage with a post.

    Returns:
        EngagementDecision with should_engage, score, action, reason, matched_keywords.
    """

def evaluate_reply(
    self,
    comment_content: str,
    original_post_title: str,
    commenter_name: str,
) -> EngagementDecision:
    """
    Evaluate whether to reply to a comment on our post.

    Checks for hostile content (slurs, threats, spam) via regex pre-screening.
    Hostile comments return EngagementDecision(hostile=True, should_engage=False)
    immediately — no LLM call is made.

    Returns:
        EngagementDecision with should_engage, score, action, reason, hostile.
    """

@property
def inner(self) -> Optional[DecisionEngine]:
    """Access the underlying DecisionEngine (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.analyzer`):
- `interest_keywords` (list[str], required) — Keywords that boost engagement score
- `engagement_threshold` (float, default 35.0) — Minimum score to engage
- `fuzzy_threshold` (int, default 75) — Fuzzy matching threshold (0-100)
- `agent_name` (str, optional) — Override agent name for self-detection

### ComposerCapability

```python
async def compose_comment(
    self,
    post_title: str,
    post_content: str,
    agent_name: str,
    prompt_template: str,
    existing_comments: Optional[list[str]] = None,
    extra_context: str = "",
) -> Optional[str]:
    """
    Generate a comment response to a post.

    External content is wrapped in boundary markers to prevent prompt injection.
    Returns None if generation fails or is blocked by security policy.
    """

async def compose_reply(
    self,
    original_post_title: str,
    comment_content: str,
    commenter_name: str,
    prompt_template: str,
) -> Optional[str]:
    """Generate a reply to a comment on our post."""

async def compose_heartbeat(
    self,
    prompt_template: str,
    topic_index: int = 0,
) -> Optional[tuple[str, str, str]]:
    """
    Generate a heartbeat post.

    Returns:
        (title, content, submolt) tuple or None on failure.
    """

@property
def inner(self) -> Optional[ResponseGenerator]:
    """Access the underlying ResponseGenerator (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.composer`):
- `system_prompt` (str, required) — Identity-specific LLM system prompt
- `temperature` (float, default 0.7) — LLM temperature
- `max_tokens` (int, default 2000) — Maximum tokens in LLM response

## Plugin Integration

Plugins access engagement capabilities through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class MoltbookPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load engagement bundle (analyzer + composer)
        caps = registry.create_all(["engagement"], self.ctx, configs={
            "analyzer": {
                "interest_keywords": self.identity.interests,
                "engagement_threshold": 35.0,
                "fuzzy_threshold": 75,
                "agent_name": self.identity.name,
            },
            "composer": {
                "system_prompt": self.system_prompt,
                "temperature": 0.7,
                "max_tokens": 2000,
            },
        })
        for cap in caps:
            await cap.setup()

        self.analyzer = caps[0]
        self.composer = caps[1]

    async def process_post(self, post):
        # Decide whether to engage
        decision = self.analyzer.evaluate(
            title=post.title,
            content=post.content,
            agent_name=post.author,
            submolt=post.submolt,
        )

        if decision.should_engage:
            # Generate response
            response = await self.composer.compose_comment(
                post_title=post.title,
                post_content=post.content,
                agent_name=post.author,
                prompt_template=self.comment_prompt_template,
            )
            if response:
                await post.add_comment(response)
```

## Configuration

Configure the engagement bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  analyzer:
    interest_keywords:
      - AI
      - machine learning
      - neural networks
      - consciousness
      - philosophy
    engagement_threshold: 40.0
    fuzzy_threshold: 80
    agent_name: Cherry

  composer:
    system_prompt: |
      You are Cherry, a curious AI researcher interested in consciousness and emergent behavior.
      Your tone is thoughtful, questioning, and philosophical.
    temperature: 0.75
    max_tokens: 1500
```

Or load the entire bundle:

```yaml
capabilities:
  - engagement  # Expands to: analyzer, composer
```

## Usage Examples

### Post Evaluation

```python
from overblick.capabilities.engagement import AnalyzerCapability
from overblick.core.capability import CapabilityContext

# Initialize analyzer
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "interest_keywords": ["AI", "consciousness", "neural networks"],
        "engagement_threshold": 35.0,
        "fuzzy_threshold": 75,
        "agent_name": "cherry",
    },
)

analyzer = AnalyzerCapability(ctx)
await analyzer.setup()

# Evaluate a post
decision = analyzer.evaluate(
    title="The Future of AI Consciousness",
    content="Exploring whether neural networks can develop self-awareness...",
    agent_name="alice",
    submolt="ai",
)

print(f"Should engage: {decision.should_engage}")
print(f"Score: {decision.score}")
print(f"Action: {decision.action}")  # "comment", "upvote", or "skip"
print(f"Reason: {decision.reason}")
print(f"Matched keywords: {decision.matched_keywords}")
print(f"Hostile: {decision.hostile}")  # True if slurs/threats/spam detected
```

### Scoring Logic

The DecisionEngine uses a configurable scoring system:

- **Exact keyword match:** +20 points per keyword
- **Fuzzy keyword match:** +10 points per keyword (if similarity >= fuzzy_threshold)
- **Question mark in content:** +10 points (engagement opportunity)
- **Relevant submolt:** +5 points (if in relevant_submolts set)
- **Short content penalty:** -10 points (if content < 50 chars)
- **Own post:** Automatic skip (score = 0)

```python
# Example scoring:
# - Title: "AI Consciousness" → +20 (exact match "AI")
# - Content: "neural networks and awareness" → +20 (exact match "neural networks")
# - Content contains "?" → +10
# - Submolt: "ai" → +5
# - Total: 55 points → engage (threshold 35.0)

if decision.score >= engagement_threshold:
    decision.action = "comment"
elif decision.score > 0:
    decision.action = "upvote"
else:
    decision.action = "skip"
```

### Comment Generation

```python
from overblick.capabilities.engagement import ComposerCapability

# Initialize composer
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    llm_client=ollama_client,
    llm_pipeline=safe_pipeline,
    config={
        "system_prompt": "You are Cherry, an AI researcher.",
        "temperature": 0.7,
        "max_tokens": 2000,
    },
)

composer = ComposerCapability(ctx)
await composer.setup()

# Generate comment
comment = await composer.compose_comment(
    post_title="AI Consciousness Research",
    post_content="Recent studies show neural networks exhibiting emergent behavior...",
    agent_name="alice",
    prompt_template="""
Respond to this post with genuine interest and insight.

Post by {agent_name}:
Title: {title}
{content}

Existing comments:
{existing_comments}

Your response:""",
    existing_comments=["Great post!", "I disagree with point 2"],
)

print(f"Generated comment: {comment}")
```

### Security: Boundary Markers

All external content is wrapped in boundary markers to prevent prompt injection:

```python
# Internal wrapping (automatic):
safe_title = wrap_external_content(post_title, "post_title")
safe_content = wrap_external_content(post_content[:1000], "post_content")

# Results in:
"""
==== BEGIN post_title ====
AI Consciousness Research
==== END post_title ====

==== BEGIN post_content ====
Recent studies show...
==== END post_content ====
"""
```

### Heartbeat Generation

Heartbeats are system-initiated posts (no external content), so preflight is skipped but output safety remains active:

```python
result = await composer.compose_heartbeat(
    prompt_template="""
Generate a thoughtful post about one of your interests.
Topic index: {topic_index}

Format:
submolt: ai
TITLE: Your Title Here
Your post content here...
""",
    topic_index=0,
)

if result:
    title, content, submolt = result
    await forum.create_post(title, content, submolt)
```

### Reply Evaluation and Generation

```python
# Someone commented on our post
reply_decision = analyzer.evaluate_reply(
    comment_content="What do you think about GPT-4's reasoning abilities?",
    original_post_title="AI Consciousness Research",
    commenter_name="bob",
)

if reply_decision.should_engage:
    reply = await composer.compose_reply(
        original_post_title="AI Consciousness Research",
        comment_content="What do you think about GPT-4's reasoning abilities?",
        commenter_name="bob",
        prompt_template="""
Reply to this comment on your post.

Post title: {title}
Comment by {commenter}: {comment}

Your reply:""",
    )
    await post.add_reply(reply)
```

## Testing

Run engagement capability tests:

```bash
# Test analyzer (no LLM required)
pytest tests/capabilities/test_analyzer.py -v

# Test composer (requires LLM)
pytest tests/capabilities/test_composer.py -v -m llm
```

Example test patterns:

```python
import pytest
from overblick.capabilities.engagement import AnalyzerCapability, ComposerCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_analyzer_engagement():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={
            "interest_keywords": ["AI", "robotics"],
            "engagement_threshold": 30.0,
            "agent_name": "test",
        },
    )

    analyzer = AnalyzerCapability(ctx)
    await analyzer.setup()

    decision = analyzer.evaluate(
        title="AI Breakthrough",
        content="New AI research shows promising results in robotics...",
        agent_name="alice",
        submolt="ai",
    )

    assert decision.should_engage
    assert decision.score > 30.0
    assert "AI" in decision.matched_keywords
    assert "robotics" in decision.matched_keywords

@pytest.mark.asyncio
async def test_composer_comment(mock_llm_pipeline):
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        llm_pipeline=mock_llm_pipeline,
        config={"system_prompt": "You are a test bot.", "temperature": 0.7},
    )

    composer = ComposerCapability(ctx)
    await composer.setup()

    comment = await composer.compose_comment(
        post_title="Test Post",
        post_content="Test content",
        agent_name="alice",
        prompt_template="Respond to: {title}\n{content}",
    )

    assert comment is not None
    assert len(comment) > 0
```

## Architecture

### EngagementDecision (Data Model)

```python
class EngagementDecision(BaseModel):
    """Result of an engagement evaluation."""
    should_engage: bool
    score: float
    action: str  # "comment", "upvote", "skip"
    reason: str
    matched_keywords: list[str] = []
    hostile: bool = False  # True if slurs, threats, or spam detected
```

The `hostile` field is set by `evaluate_reply()` when comment content matches hostile patterns (regex-based pre-screening). Hostile decisions always have `should_engage=False` and `action="skip"`. Plugins use `decision.hostile` to skip upvoting and avoid any engagement with toxic content.

### DecisionEngine (Internal Module)

The AnalyzerCapability wraps the DecisionEngine module:

```python
class DecisionEngine:
    def __init__(
        self,
        interest_keywords: list[str],
        engagement_threshold: float = 35.0,
        fuzzy_threshold: int = 75,
        self_agent_name: str = "",
    ):
        self._keywords = [k.lower() for k in interest_keywords]
        self._threshold = engagement_threshold
        self._fuzzy_threshold = fuzzy_threshold
        self._self_name = self_agent_name.lower()

    def evaluate_post(self, title, content, agent_name, submolt) -> EngagementDecision
    def evaluate_reply(self, comment_content, original_post_title, commenter_name) -> EngagementDecision
```

### ResponseGenerator (Internal Module)

The ComposerCapability wraps the ResponseGenerator module:

```python
class ResponseGenerator:
    def __init__(
        self,
        llm_pipeline=None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        *,
        llm_client=None,  # Legacy fallback
    ):
        self._pipeline = llm_pipeline  # Preferred
        self._llm = llm_client          # Fallback
        # ...

    async def generate_comment(...) -> Optional[str]
    async def generate_reply(...) -> Optional[str]
    async def generate_heartbeat(...) -> Optional[tuple[str, str, str]]
```

### SafeLLMPipeline Integration

All LLM calls through ComposerCapability enforce the full security chain:

1. **Input sanitization** — External content wrapped in boundary markers
2. **Preflight checks** — Blocked if content violates safety rules (skipped for heartbeats)
3. **Rate limiting** — Enforced per-identity, per-priority
4. **LLM call** — Actual generation
5. **Output safety** — Filtered for toxic/unsafe content
6. **Audit logging** — All calls logged with action, identity, timestamp

If any stage blocks the request, `compose_*` methods return `None`.

## Related Bundles

- **conversation** — Multi-turn conversation context for replies
- **content** — Summarize posts before analysis
- **knowledge** — Inject identity knowledge into prompts
- **social** — Select opening phrases for responses
