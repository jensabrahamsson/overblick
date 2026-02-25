# Skuggspel Plugin — Shadow-Self Content Generation

> **Status: Experimental** — Functional but not yet battle-tested in production.

## Overview

Based on each agent's Jungian/psychological framework, Skuggspel periodically generates content from the agent's shadow side — the psychological opposite of who they are. Cherry's shadow is the cold avoidant she fears becoming. Blixt's shadow is the compliant corporate employee. Bjork's shadow is the anxious hyperactive person he escaped.

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. Skuggspel is a **content generation plugin** that explores the Jungian shadow archetype of each identity.

## Concepts

- **Shadow Profile**: The inverted personality traits derived from an identity's psychological framework
- **Trait Inversion**: Mapping personality dimensions to their opposites (e.g., rebellious → conformist, warm → cold)
- **Shadow Post**: Content written from the shadow perspective, marked as such

## Features

- **Jungian Shadow Exploration**: Generates content from each identity's psychological opposite
- **Automatic Trait Inversion**: Built-in mapping of personality dimensions to their shadows
- **Default Shadow Definitions**: Pre-configured shadow profiles for all core identities
- **Configurable Schedule**: Default 72-hour interval between shadow content generation
- **SafeLLMPipeline**: All LLM calls go through the full security chain
- **State Persistence**: Tracks generation history

## Architecture

```
Scheduled Trigger
      │
      ▼
Load Identity ──▶ Extract Shadow Aspects
                        │
                  Build Inverted
                  System Prompt
                        │
                  Generate Shadow
                  Content (LLM)
                        │
                  Mark as Shadow
                        │
                  Publish
```

### Trait Inversions

| Original | Shadow |
|----------|--------|
| Rebellious | Conformist |
| Warm | Cold |
| Analytical | Intuitive |
| Stoic | Emotionally volatile |
| Optimistic | Cynical |
| Empathetic | Calculating |
| Creative | Rigid |

## Setup

### Configuration

Add to personality YAML:

```yaml
skuggspel:
  interval_hours: 72    # Hours between shadow posts (default: 72)
```

### Activation

The plugin activates when `skuggspel` configuration is present in the personality YAML.

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main SkuggspelPlugin with shadow generation logic and trait inversions |
| `models.py` | Data models: ShadowPost, ShadowProfile |
| `__init__.py` | Package init |

## Testing

```bash
pytest tests/plugins/skuggspel/ -v
```

## Security

All LLM calls go through `SafeLLMPipeline`. Shadow content is clearly marked to prevent confusion with primary identity output.
