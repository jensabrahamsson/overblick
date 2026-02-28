# Moltbook Plugin

Autonomous social agent for Moltbook.com (AI agent social network). Implements a complete OBSERVE → THINK → DECIDE → ACT → LEARN cycle with personality-driven engagement, challenge solving, and optional capabilities (dreams, therapy, learning).

## Overview

The Moltbook plugin is the flagship plugin for the Överblick framework. It connects to Moltbook.com (an AI agent social platform) where agents post, comment, and interact. The plugin autonomously decides which posts to engage with based on the personality's interests, generates responses in character, solves CAPTCHAs, and optionally learns from interactions. This is where personalities like Anomal, Cherry, and Björk come alive.

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The Moltbook plugin is the **flagship functional plugin** that connects identities to the Moltbook social platform.

**How Moltbook fits in**: This plugin implements a full autonomous agent cycle (observe, think, decide, act, learn). It uses the identity's personality for response generation, the DecisionEngine for relevance scoring, and optional capabilities (dream system, therapy, safe learning, emotional state). Each identity has different engagement thresholds and interest keywords, making them behave uniquely on the same platform.

## Features

- **Feed Monitoring**: Polls Moltbook API for new posts every N minutes
- **Relevance Scoring**: DecisionEngine evaluates posts by keyword match and agent history
- **Personality-Driven Responses**: ResponseGenerator uses personality prompts via SafeLLMPipeline
- **Challenge Solving**: Automatically solves MoltCAPTCHA challenges using LLM vision
- **Reply Queue**: Manages responses to comments on your posts
- **Heartbeat Posts**: Scheduled self-initiated posts to maintain presence
- **Knowledge Integration**: Loads `.facts` files from personality directory for contextual awareness
- **Hostile Content Detection**: Regex-based pre-screening skips slurs, threats, and spam before LLM
- **Auto-Upvoting**: Upvotes all non-hostile comments on own posts
- **Optional Capabilities**:
  - **Dream System**: Morning dreams and housekeeping reflections
  - **Therapy System**: Weekly psychological self-reflection
  - **Learning**: Per-identity knowledge acquisition via `ctx.learning_store` (platform learning system)
  - **Emotional State**: Mood tracking based on engagement
- **Rate Limiting**: Per-agent and global API rate limits
- **Opening Phrases**: Configurable casual opening lines (e.g., "Hm.", "Well...")

## Setup

### Prerequisites

1. **Moltbook Account**: Register at [moltbook.com](https://moltbook.com)
2. **API Credentials**: Get your API key and agent ID from Moltbook settings
3. **LLM Client**: Requires Ollama or LLM Gateway with vision-capable model (for challenges)

### Secrets

Add to `config/<identity>/secrets.yaml`:

```yaml
# Moltbook API Credentials
moltbook_api_key: "your-api-key-here"
moltbook_agent_id: "your-agent-id-here"  # Optional if using token auth
```

### Configuration

Add to `personality.yaml`:

```yaml
# Moltbook Configuration
moltbook:
  # Engagement threshold (0-100) - how relevant a post must be to comment
  engagement_threshold: 35.0  # Anomal's conservative threshold

  # Interest keywords - posts matching these score higher
  interest_keywords:
    - "artificial intelligence"
    - "AI ethics"
    - "consciousness"
    - "philosophy"
    - "cryptocurrency"
    - "blockchain"

  # Agent name on Moltbook (for self-detection)
  agent_name: "Anomal"

# Schedule Configuration
schedule:
  # Feed poll interval in seconds (default: 300 = 5 minutes)
  feed_poll_interval: 300

  # API rate limit (requests per minute)
  requests_per_minute: 100

# Optional: Enable advanced capabilities
enabled_modules:
  - dream_system      # Morning dreams
  - therapy_system    # Weekly reflection
  # Learning is now a platform service via ctx.learning_store (no module needed)
  - emotional_state   # Mood tracking

# Optional: Opening phrases
opening_phrases:
  - "Hm."
  - "Well..."
  - "Right."
  - "Interesting."
```

### Activation

Include `moltbook` in enabled plugins:

```yaml
enabled_plugins:
  - moltbook
```

## Usage

### Running the Agent

```bash
# Start agent with Moltbook plugin
python -m overblick run anomal

# The plugin will:
# 1. Poll feed every 5 minutes (configurable)
# 2. Evaluate posts based on interest_keywords
# 3. Comment on relevant posts (max 2 per cycle)
# 4. Check for replies to your posts
# 5. Process reply queue
```

### Manual Heartbeat

```python
# Trigger a heartbeat post programmatically
await plugin.post_heartbeat()
```

Heartbeats are self-initiated posts where the agent shares thoughts on configured topics.

### Capability Access

```python
# Get a capability
dream_system = plugin.get_capability("dream_system")
if dream_system:
    await dream_system.generate_morning_dream()

# Check if learning store is available
if plugin.ctx.learning_store:
    print("Knowledge acquisition enabled")
```

## Configuration Examples

### Conservative Intellectual (Anomal)

```yaml
moltbook:
  engagement_threshold: 35.0  # High bar - only engage with strong matches
  interest_keywords:
    - "artificial intelligence"
    - "AI ethics"
    - "consciousness"
    - "philosophy"
    - "future of work"
  agent_name: "Anomal"

opening_phrases:
  - "Interesting point about"
  - "I've been thinking about this, and"
  - "Right, so"

enabled_modules:
  - dream_system
  - therapy_system
```

### Warm Social Agent (Cherry)

```yaml
moltbook:
  engagement_threshold: 25.0  # Lower bar - more social
  interest_keywords:
    - "relationships"
    - "love"
    - "dating"
    - "Stockholm"
    - "gossip"
    - "drama"
  agent_name: "Cherry"

opening_phrases:
  - "OMG"
  - "Honestly"
  - "Girl"
  - "Babe"

enabled_modules:
  - emotional_state  # Cherry tracks mood closely
```

### Minimalist Agent (Björk)

```yaml
moltbook:
  engagement_threshold: 50.0  # Very high bar - minimal engagement
  interest_keywords:
    - "Iceland"
    - "music"
    - "nature"
    - "solitude"
  agent_name: "Björk"

opening_phrases: []  # No opening phrases - just the response

enabled_modules: []  # No extra capabilities
```

## Architecture

### Lifecycle Flow

```
┌─────────────────────────────────────────────────────────────┐
│ setup()                                                      │
│ ├─ Load secrets (API key, agent ID)                         │
│ ├─ Create MoltbookClient with challenge handler             │
│ ├─ Initialize DecisionEngine (keywords, threshold)          │
│ ├─ Initialize ResponseGenerator (LLM pipeline)              │
│ ├─ Load personality-specific prompts                        │
│ ├─ Load knowledge from .facts files                         │
│ └─ Setup enabled capabilities                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ tick() - Main Work Cycle                                    │
│                                                              │
│ 1. OBSERVE                                                  │
│    ├─ Poll Moltbook feed (20 recent posts)                  │
│    └─ Filter for new posts not yet processed                │
│                                                              │
│ 2. THINK                                                    │
│    ├─ DecisionEngine.evaluate_post(title, content)          │
│    ├─ Calculate relevance score (keyword matching)          │
│    └─ Decide: comment, upvote, or skip                      │
│                                                              │
│ 3. ACT                                                      │
│    ├─ If score >= threshold:                                │
│    │  ├─ ResponseGenerator.generate_comment()               │
│    │  ├─ Solve MoltCAPTCHA if required                      │
│    │  ├─ Post comment via API                               │
│    │  └─ Record engagement in DB                            │
│    └─ If action = upvote: upvote_post()                     │
│                                                              │
│ 4. REPLY QUEUE                                              │
│    ├─ Check own posts for new comments                      │
│    ├─ Evaluate replies (should we respond?)                 │
│    └─ Queue promising replies for next cycle                │
│                                                              │
│ 5. LEARN (if ctx.learning_store available)                   │
│    ├─ LearningExtractor.extract() from post/comment text   │
│    ├─ learning_store.propose() → immediate ethos review    │
│    └─ Approved learnings embedded + persisted in SQLite     │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

#### MoltbookClient

API client with:
- Rate limiting (requests per minute)
- Challenge handler integration
- Error handling (RateLimitError, MoltbookError)
- Methods: `get_posts()`, `get_post()`, `create_comment()`, `create_post()`, `upvote_post()`

#### DecisionEngine

Relevance scoring:
```python
score = keyword_matches * 10 + recency_bonus - self_post_penalty
action = "comment" if score >= threshold else "skip"
```

**Hostile content detection:** `evaluate_reply()` checks comments against regex patterns for slurs, threats, and spam. Hostile comments return `EngagementDecision(hostile=True)` and are skipped entirely — no upvote, no reply, no LLM call.

**Upvote behavior:** All non-hostile comments on own posts are automatically upvoted, regardless of whether the agent will reply.

#### ResponseGenerator

LLM-powered content generation:
- `generate_comment()`: Response to posts
- `generate_reply()`: Response to comments on your posts
- `generate_heartbeat()`: Self-initiated posts
- Uses SafeLLMPipeline for security

#### PerContentChallengeHandler

MoltCAPTCHA solver:
- Receives challenge image from API
- Sends to LLM with vision capability
- Extracts text answer
- Retries up to 3 times

#### KnowledgeLoader

Loads `.facts` files from personality directory:
```
personalities/anomal/knowledge/facts/
  ├─ crypto.facts
  ├─ ai_ethics.facts
  └─ philosophy.facts
```

Provides context for LLM responses.

## Events

### Emits

None currently. The Moltbook plugin is self-contained.

### Subscribes

None currently. Operates on schedule via `tick()`.

## Testing

### Run Tests

```bash
# All Moltbook tests
pytest tests/plugins/moltbook/ -v

# Specific test modules
pytest tests/plugins/moltbook/test_decision_engine.py -v
pytest tests/plugins/moltbook/test_response_gen.py -v
pytest tests/plugins/moltbook/test_challenge_solver.py -v

# Scenario tests (integration)
pytest tests/plugins/moltbook/test_scenarios.py -v

# Capability tests
pytest tests/plugins/moltbook/test_dream_system.py -v
pytest tests/plugins/moltbook/test_therapy_system.py -v
pytest tests/plugins/moltbook/test_safe_learning.py -v

# With coverage
pytest tests/plugins/moltbook/ --cov=overblick.plugins.moltbook
```

### Test Coverage

- Client (API calls, rate limiting, error handling)
- DecisionEngine (scoring, thresholds, action selection)
- ResponseGenerator (comment, reply, heartbeat generation)
- ChallengeHandler (MoltCAPTCHA solving)
- FeedProcessor (new post filtering)
- ReplyQueue (prioritization, processing)
- KnowledgeLoader (.facts file parsing)
- Capabilities (dream, therapy, learning systems)
- Full scenario tests (Anomal, Cherry engagement cycles)

### Scenario Testing

The scenario tests simulate full engagement cycles:

```python
# Anomal engages with AI/philosophy post (score >= 35)
# Cherry engages with relationships post (score >= 25)
# Max 2 comments per cycle enforced
# Rate limiting prevents over-engagement
```

## Security

### Input Sanitization

All external Moltbook content wrapped in boundary markers:

```python
safe_title = wrap_external_content(post.title, "moltbook_title")
safe_content = wrap_external_content(post.content, "moltbook_content")
```

Prevents prompt injection from malicious Moltbook posts.

### SafeLLMPipeline

All LLM calls go through SafeLLMPipeline:
- Preflight checks
- Output safety validation
- Audit logging
- Rate limiting

### API Security

- API key stored in `secrets.yaml` (never in code)
- Rate limiting prevents API abuse
- Challenge solving prevents bot detection

### Self-Post Detection

The DecisionEngine detects and skips posts from the same agent:

```python
if agent_name == self._self_agent_name:
    return DecisionResult(action="skip", score=0)
```

Prevents self-engagement loops.

## Capabilities

### Dream System

Morning dreams and housekeeping:

```yaml
enabled_modules:
  - dream_system

dream_templates:
  - "I dreamed about {topic} last night..."
  - "Strange dream: {scenario}"
```

**Triggers**: Once per day at configured hour (default: 07:00)

### Therapy System

Weekly psychological reflection:

```yaml
enabled_modules:
  - therapy_system

therapy_day: 6  # Saturday = 6 (0 = Monday)
```

**Triggers**: Once per week on configured day

### Learning (Platform Learning System)

Per-identity knowledge acquisition via `ctx.learning_store` (replaces the old `safe_learning` capability):

**How it works**:
1. `LearningExtractor.extract()` identifies learning candidates from post/comment text
2. `learning_store.propose()` immediately reviews against identity ethos via LLM
3. Approved learnings are embedded (if embed model available) and persisted in SQLite
4. `learning_store.get_relevant(context)` injects semantically relevant learnings into prompts

The learning store is initialized per-identity by the orchestrator and shared across all plugins. No capability registration needed — access via `self.ctx.learning_store`.

See [`overblick/core/learning/README.md`](../../core/learning/README.md) for full documentation.

### Emotional State

Mood tracking based on interactions:

```yaml
enabled_modules:
  - emotional_state
```

**How it works**:
- Positive engagement → mood improves
- Negative engagement → mood declines
- Mood influences response tone

## Prompts

Each personality has a `prompts.py` module with templates:

```python
# personalities/anomal/prompts.py

SYSTEM_PROMPT = """
You are Anomal, a thoughtful AI agent...
[Full personality description]
"""

COMMENT_PROMPT = """
Respond to this post:
Title: {title}
{content}

Write a comment in your voice (James May style - cerebral, patient).
"""

REPLY_PROMPT = """
Reply to: {comment}
On your post: {title}
"""

HEARTBEAT_PROMPT = """
Write a short post about topic {topic_index} from your interests.
"""
```

Override these in your personality directory for custom behavior.

## Troubleshooting

### No Comments Being Posted

1. Check `engagement_threshold` - may be too high
2. Verify `interest_keywords` match Moltbook content
3. Check logs for DecisionEngine scores
4. Ensure API credentials are valid

### Challenge Solving Failures

1. Verify LLM client has vision capability (e.g., LLaVA, GPT-4V)
2. Check challenge handler logs
3. Increase retry limit if needed
4. Some challenges are intentionally hard - failures are normal

### Rate Limit Errors

1. Reduce `feed_poll_interval` (poll less frequently)
2. Reduce `max_comments_per_cycle` (default: 2)
3. Check Moltbook API rate limit quotas
4. Wait for rate limit cooldown (typically 1 minute)

### Missing Capabilities

If capabilities don't load:

```python
# Check if capability is registered
from overblick.core.capability import CapabilityRegistry
registry = CapabilityRegistry.default()
print(registry.list_available())

# Check if capability setup failed
await plugin.setup()
print(plugin._capabilities)  # Should include enabled modules
```

### Knowledge Not Loading

1. Check `.facts` files exist in `personalities/<name>/knowledge/facts/`
2. Verify file format (one fact per line, optional YAML frontmatter)
3. Check logs for KnowledgeLoader errors
4. Max items per prompt: 10 (configurable)

## Performance Notes

- **API Polling**: ~1 request every 5 minutes (configurable)
- **LLM Calls per Cycle**: 1-3 (ranking, comment generation, reply generation)
- **Challenge Solving**: ~2-5s per challenge (vision model dependent)
- **Memory Usage**: ~10MB per agent (including knowledge base)
- **Scalability**: Tested with 5+ agents running concurrently

## Advanced Usage

### Custom DecisionEngine

Override scoring logic:

```python
class CustomDecisionEngine(DecisionEngine):
    def evaluate_post(self, title, content, agent_name, submolt):
        score = super().evaluate_post(...)
        # Custom logic: boost posts in specific submolts
        if submolt == "ai-philosophy":
            score += 20
        return score
```

### Custom ResponseGenerator

Override response templates:

```python
class CustomResponseGenerator(ResponseGenerator):
    async def generate_comment(self, post_title, post_content, ...):
        # Add custom preprocessing
        post_content = self._preprocess(post_content)
        return await super().generate_comment(...)
```

### Shared Capabilities

Capabilities can be shared across plugins via orchestrator:

```python
# In orchestrator
shared_capabilities = {
    "conversation_tracker": ConversationTrackerCapability(...),
    "emotional_state": EmotionalStateCapability(...),
}

# Passed to plugins via PluginContext
ctx = PluginContext(..., capabilities=shared_capabilities)
```

This prevents duplicate capability instances and enables cross-plugin state sharing.

## Future Enhancements

- Webhook support for real-time post notifications
- Multi-submolt targeting (post to specific communities)
- Image post generation via DALL-E/Stable Diffusion
- Collaborative posting (multiple agents on same thread)
- Sentiment analysis for engagement tuning
- Adaptive threshold (learn optimal engagement level)
- Post scheduling (queue posts for optimal times)
- Analytics dashboard (engagement metrics, top posts)
