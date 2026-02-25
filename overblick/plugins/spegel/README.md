# Spegel Plugin — Inter-Agent Psychological Profiling

> **Status: Experimental** — Functional but not yet battle-tested in production.

## Overview

Each agent writes a psychological profile of another agent based on their personality definition and psychological framework. The profiled agent then reads the profile and responds with a reflection. Self-awareness through others' eyes.

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. Spegel is a **relationship plugin** that creates inter-agent psychological understanding through profiling and reflection.

## Concepts

- **Spegel Pair**: Two identities — one observer, one target
- **Profile**: The observer's psychological analysis of the target
- **Reflection**: The target's response after reading their profile
- **Identity Pairs**: All possible observer/target combinations from available identities

## Features

- **Mutual Profiling**: Each identity profiles others and receives profiles of itself
- **Two-Phase Generation**: Profile first, then reflection — creating a dialogue
- **Weekly Schedule**: Default 168-hour (7-day) interval between profiling rounds
- **Full Identity Discovery**: Automatically discovers all available identities
- **SafeLLMPipeline**: All LLM calls go through the full security chain
- **State Persistence**: Tracks which pairs have been profiled and when

## Architecture

```
Scheduled Trigger
      │
      ▼
Discover Identity Pairs
      │
For each pair (Observer, Target):
      │
      ├──▶ Load Observer Identity
      │         │
      │    Generate Profile of Target
      │         │
      ├──▶ Load Target Identity
      │         │
      │    Generate Reflection on Profile
      │         │
      └──▶ Store Pair (Profile + Reflection)
              │
        Display on Dashboard
```

## Setup

### Configuration

Add to personality YAML:

```yaml
spegel:
  interval_hours: 168           # Hours between rounds (default: 168 / weekly)
  pairs:                        # Optional: specific pairs (default: all combinations)
    - ["anomal", "cherry"]
    - ["blixt", "bjork"]
```

### Activation

The plugin activates when `spegel` configuration is present in the personality YAML.

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main SpegelPlugin with pair management and two-phase generation |
| `models.py` | Data models: Profile, Reflection, SpegelPair |
| `__init__.py` | Package init |

## Testing

```bash
pytest tests/plugins/spegel/ -v
```

## Security

All LLM calls go through `SafeLLMPipeline`. Profile and reflection content is generated from personality definitions only — no external content is involved.
