# Kontrast Plugin — Multi-Perspective Content Engine

> **Status: Experimental** — Functional but not yet battle-tested in production.

## Overview

When a news topic emerges, ALL available identities write their take simultaneously. The results are published side-by-side as a "Kontrast" piece on the dashboard. Same event, multiple worldviews.

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. Kontrast is a **content generation plugin** that leverages the full identity stable to produce multi-perspective commentary on current events.

## Concepts

- **Kontrast Piece**: A collection of perspectives from multiple identities on the same topic
- **Perspective Entry**: One identity's take on a topic, written in their voice
- **Fan-Out**: The process of sending the same topic to all identities in parallel

## Features

- **RSS-Triggered Topics**: Reuses AI Digest feed infrastructure for topic discovery
- **Full Identity Fan-Out**: Every available identity writes their perspective
- **Side-by-Side Display**: Dashboard presents all viewpoints simultaneously
- **Boundary-Marked Input**: All external RSS content wrapped in security markers
- **SafeLLMPipeline**: All LLM calls go through the full security chain
- **State Persistence**: Tracks processed topics to prevent duplicates

## Architecture

```
RSS Feeds ──▶ Topic Selection ──▶ Fan-Out to Identities
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
              Identity A          Identity B          Identity C
              (LLM call)          (LLM call)          (LLM call)
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        │
                                  Assemble Piece
                                        │
                                  Publish to Dashboard
```

## Setup

### Configuration

Add to personality YAML:

```yaml
kontrast:
  feeds:                              # RSS feeds for topic discovery
    - "https://feeds.arstechnica.com/arstechnica/technology-lab"
  interval_hours: 24                  # Hours between pieces (default: 24)
  min_articles: 3                     # Minimum articles before generating (default: 3)
```

### Activation

The plugin activates when `kontrast` configuration is present in the personality YAML.

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main KontrastPlugin with RSS polling, fan-out, and assembly |
| `models.py` | Data models: KontrastPiece, PerspectiveEntry |
| `__init__.py` | Package init |

## Testing

```bash
pytest tests/plugins/kontrast/ -v
```

## Security

All external RSS content (titles, summaries) is wrapped in boundary markers via `wrap_external_content()` before being sent to the LLM. All LLM calls go through `SafeLLMPipeline`.
