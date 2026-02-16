# Överblick Skill Compiler

A Claude Code meta-skill that compiles specifications into complete, production-grade Överblick components — plugins, capabilities, and personalities — with full implementation, tests, and registry wiring.

## What It Does

The skill compiler is NOT a scaffolder. It produces **working code**. Given a specification (either a SKILL.md file or a free-form description), it:

1. **Analyzes** the specification to determine which components are needed (plugin, capability, personality, or a combination)
2. **Generates** full production-grade code using framework templates with all security patterns baked in
3. **Writes** comprehensive test suites (setup, tick, teardown, security verification)
4. **Wires** registries so components are discoverable by the framework
5. **Verifies** by running tests and fixing any failures

The only TODOs left in generated code are for external-API-specific logic that requires credentials or documentation unavailable at generation time.

## How to Use

### Method 1: SKILL.md Spec File

Create a spec file with YAML frontmatter and markdown sections:

```yaml
---
name: "slack"
needs_secrets: [slack_bot_token]
external_api: "https://slack.com/api"
needs_events: [message_received]
---
## Purpose
Slack integration for monitoring and responding to messages in configured channels.

## Behavior
- On setup: Load API token, connect to WebSocket
- On tick: Process queued messages, respond if relevant
- On teardown: Close connection gracefully
```

Then tell Claude Code:

```
Compile this SKILL.md into a working plugin
```

Or:

```
Build from spec: [paste or reference the SKILL.md]
```

### Method 2: Free-Form Description

Just describe what you want:

```
Generate a plugin from description: An RSS feed monitor that polls configured feeds,
summarizes new articles using LLM, and posts summaries to Moltbook
```

The compiler will:
1. Apply the decision tree to determine components needed
2. Ask at most 3 targeted questions to fill gaps
3. Generate everything

### Trigger Phrases

Any of these will activate the skill:

| Phrase | Example |
|--------|---------|
| `compile skill` | "Compile this SKILL.md into components" |
| `build skill` | "Build a skill for Discord integration" |
| `build from spec` | "Build from spec: a sentiment analysis capability" |
| `skill compiler` | "Use the skill compiler for this feature" |
| `generate plugin from description` | "Generate plugin from description: Webhooks receiver" |
| `scaffold from spec` | "Scaffold from spec: RSS monitor with summarization" |
| `generate components` | "Generate components for a new Matrix integration" |

## SKILL.md Spec Format

### YAML Frontmatter

```yaml
---
name: "my-feature"                    # Required: lowercase, hyphenated
type: "plugin"                         # Optional: plugin|capability|personality|composite
bundle: "engagement"                   # For capabilities: which bundle to join
needs_llm: true                        # Whether the component uses LLM
needs_secrets: [api_key, webhook_secret]  # Secret keys needed in setup()
needs_events: [message_received]       # Events listened to via on_event()
emits_events: [analysis_complete]      # Events published to event_bus
needs_tick: true                       # Whether tick() does meaningful work
external_api: "https://api.example.com"  # External API URL/description
capabilities_used: [summarizer]        # Existing capabilities consumed
---
```

All fields except `name` are optional. The compiler infers defaults:
- `type`: Auto-detected from `external_api` (→ plugin), `bundle` (→ capability), etc.
- `needs_llm`: Defaults to `true` for plugins
- `needs_tick`: Defaults to `true` for plugins

### Markdown Sections

After the frontmatter, include any of these sections:

| Section | What It Provides |
|---------|-----------------|
| **Purpose** | Module docstring (1-3 sentences) |
| **Behavior** | Setup/tick/event logic → drives code generation |
| **Configuration** | identity.yaml keys → config parsing code |
| **External API** | Endpoints, auth, rate limits → HTTP client code |
| **Events** | Event contracts → on_event() dispatch code |
| **Security** | Special security considerations beyond defaults |
| **Examples** | I/O examples → test case generation |

## What Gets Generated

### For a Plugin

| File | Purpose |
|------|---------|
| `overblick/plugins/<name>/plugin.py` | Main plugin class with full lifecycle |
| `overblick/plugins/<name>/__init__.py` | Re-export plugin class |
| `overblick/core/plugin_registry.py` | Updated `_KNOWN_PLUGINS` whitelist |
| `tests/plugins/<name>/__init__.py` | Package marker |
| `tests/plugins/<name>/conftest.py` | Test fixtures (identity, context, mocks) |
| `tests/plugins/<name>/test_<name>.py` | Tests: setup, tick, teardown, security |

### For a Capability

| File | Purpose |
|------|---------|
| `overblick/capabilities/<bundle>/<name>.py` | Capability class with lifecycle |
| `overblick/capabilities/<bundle>/__init__.py` | Bundle init (if new bundle) |
| `overblick/capabilities/__init__.py` | Updated registry, bundles, `__all__` |
| `tests/capabilities/test_<name>.py` | Tests: creation, setup, methods, LLM |

### For a Personality

| File | Purpose |
|------|---------|
| `overblick/identities/<name>/personality.yaml` | Full character YAML |

### For Composite Features

Multiple components are generated in dependency order: personality first, then capabilities, then plugins.

## Security Guarantees

Every generated component includes these security patterns (non-negotiable):

1. **`wrap_external_content()`** — All external data wrapped in boundary markers
2. **`ctx.llm_pipeline`** — All LLM calls go through SafeLLMPipeline (never raw client)
3. **`result.blocked` handling** — Every pipeline call checks for blocked results
4. **`ctx.get_secret()`** — Secrets loaded via framework, never hardcoded
5. **`ctx.audit_log.log()`** — Setup and significant actions audited
6. **Quiet hours check** — First thing in tick(), before any LLM work
7. **No secret logging** — Key names only, never values

## Component Decision Tree

The compiler uses this logic to determine what to generate:

```
External API?        → Plugin
Reusable behavior?   → Capability
New character/persona? → Personality
Combination?         → Generate all needed
```

Key signals:
- **Has an API key** → definitely needs a Plugin
- **Two plugins could use it** → make it a Capability
- **About WHO the agent is** → needs a Personality
- **Most real features** need 2+ components (composite)

## File Structure

```
.claude/skills/overblick-skill-compiler/
├── SKILL.md                              # Main skill definition
└── references/
    ├── component-decision-tree.md        # When to create what
    ├── plugin-template.md                # Full plugin code template
    ├── capability-template.md            # Full capability code template
    ├── personality-template.md           # Full personality YAML template
    ├── test-templates.md                 # Test + conftest templates
    ├── registry-wiring.md                # Exact registry update patterns
    └── skill-spec-format.md              # SKILL.md input spec schema
```

## Examples

### Example 1: Simple Plugin

```
Generate plugin from description: A webhook receiver that accepts HTTP POST
requests, validates the signature, and forwards the payload to the event bus.
Secret: webhook_signing_key.
```

Generates: Plugin + tests + registry entry.

### Example 2: Capability + Plugin

```
Build from spec: A sentiment analysis system. The analysis itself should be
a reusable capability (engagement bundle). A Telegram plugin should use it
to annotate incoming messages with sentiment scores.
```

Generates: SentimentCapability + updated engagement bundle + TelegramSentimentPlugin + all tests + both registry updates.

### Example 3: Full Agent Identity

```yaml
---
name: "sage"
type: "composite"
needs_llm: true
needs_secrets: [matrix_access_token]
external_api: "https://matrix.org/_matrix/client"
needs_events: [message_received]
---
## Purpose
A philosophical counselor agent on Matrix. Sage listens to conversations,
offers thoughtful perspectives when asked, and maintains a journal of insights.

## Behavior
- Personality: Calm, wise, uses metaphors, quotes philosophers
- Plugin: Matrix client integration with room monitoring
- Capability: Insight journaling (knowledge bundle) — tracks conversation themes
```

Generates: Sage personality YAML + InsightCapability in knowledge bundle + MatrixPlugin + all tests + all registry updates.

## Relationship to Other Skills

| Skill | Role |
|-------|------|
| **overblick-skill-compiler** | Automated code generation from specs |
| overblick-plugin-helper | Interactive guidance for plugin development |
| overblick-capability-helper | Interactive guidance for capability development |
| overblick-personality-helper | Interactive guidance for personality design |

The compiler uses the same framework knowledge as the helpers but automates the process end-to-end. Use the **helpers** when you want interactive guidance and learning. Use the **compiler** when you know what you want and need it built fast.
