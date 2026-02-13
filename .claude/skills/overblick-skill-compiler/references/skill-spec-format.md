# SKILL.md Input Spec Schema

When the user provides a SKILL.md file as input to the compiler, it follows this schema.

## YAML Frontmatter

```yaml
---
# REQUIRED
name: "my-feature"          # Lowercase, hyphenated. Used for directories and registry keys.

# OPTIONAL (auto-detected if omitted)
type: "plugin"               # One of: plugin, capability, personality, composite
                              # If omitted, the compiler applies the decision tree.

# CAPABILITY-SPECIFIC
bundle: "content"             # Which capability bundle to add to.
                              # Use existing bundle name or a new one.

# BEHAVIORAL FLAGS
needs_llm: true               # Whether the component uses LLM calls.
needs_secrets:                 # List of secret keys required in setup().
  - api_key
  - webhook_secret
needs_events:                  # Events this component listens to via on_event().
  - message_received
  - post_created
emits_events:                  # Events this component publishes to event_bus.
  - analysis_complete
  - alert_triggered
needs_tick: true               # Whether tick() does meaningful work.
                               # If false, tick() is a no-op.

# EXTERNAL INTEGRATION
external_api: "https://api.example.com"  # Base URL or description of external API.

# DEPENDENCIES
capabilities_used:             # Existing capabilities this plugin consumes.
  - summarizer
  - conversation_tracker
---
```

## Markdown Sections

After the frontmatter, the spec contains markdown sections. All sections are optional — the compiler uses what's available and infers the rest.

### Purpose

What the component does in 1-3 sentences. This becomes the module docstring.

```markdown
## Purpose
Monitor a Slack workspace for messages matching specific patterns and
respond using the agent's personality. Supports threaded replies and
direct messages.
```

### Behavior

Detailed behavioral specification. What happens during setup, tick, event handling. This is the primary input for code generation.

```markdown
## Behavior
- On setup: Load Slack API token, create WebSocket connection, join configured channels
- On tick: Process any queued messages that arrived since last tick
- On message_received: Evaluate relevance using the decision engine, generate response if above threshold
- On teardown: Close WebSocket gracefully, log session summary
```

### Configuration

Identity.yaml config keys this component reads from `identity.raw_config`.

```markdown
## Configuration
- `slack_channels`: List of channel IDs to monitor (default: all)
- `response_threshold`: Minimum relevance score to respond (default: 50)
- `max_responses_per_hour`: Rate limit for responses (default: 10)
```

### External API

API details — endpoints, authentication, rate limits. This helps generate the HTTP client code.

```markdown
## External API
- Base URL: https://slack.com/api
- Auth: Bearer token (secret: `slack_bot_token`)
- Rate limit: 1 request per second (Tier 2)
- Key endpoints:
  - POST /chat.postMessage — send a message
  - GET /conversations.history — get channel messages
  - POST /reactions.add — add emoji reaction
```

### Events

Event contracts — what data each event carries.

```markdown
## Events
### Listens to:
- `message_received`: `{channel: str, user: str, text: str, ts: str}`

### Emits:
- `slack_response_sent`: `{channel: str, text: str, thread_ts: str}`
```

### Security

Special security considerations beyond the standard rules.

```markdown
## Security
- All message content from Slack must be wrapped with wrap_external_content()
- User IDs must not be logged in plaintext (use hash)
- Rate limiting must respect Slack's API limits in addition to internal rate limiter
```

### Examples

Example inputs and expected outputs — helps generate test cases.

```markdown
## Examples
Input: User posts "What's the best way to encrypt files?" in #security
Expected: Agent responds with a relevant comment using personality voice, mentioning encryption tools

Input: User posts "gm" in #general
Expected: Agent skips (below relevance threshold)
```

## Minimal Spec

The absolute minimum required for the compiler to work:

```yaml
---
name: "slack"
needs_secrets: [slack_bot_token]
external_api: "https://slack.com/api"
---
## Purpose
Slack integration for monitoring and responding to messages.
```

The compiler will infer:
- Type: `plugin` (because external_api is specified)
- needs_llm: `true` (default for plugins)
- needs_tick: `true` (default for plugins)
- Full security patterns, lifecycle methods, tests
