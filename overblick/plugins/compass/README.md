# Compass Plugin — Identity Drift Detector

> **Status: Experimental** — Functional but not yet battle-tested in production.

## Overview

Compass monitors each identity's outputs over time and detects when they drift from their personality definition. It uses stylometric analysis (sentence length, vocabulary distribution, punctuation patterns) combined with semantic consistency checks.

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. Compass is an **analysis plugin** that acts as both a security tool (catches identity corruption or prompt injection) and a quality tool (catches personality flattening over time).

## Concepts

- **Stylometric Baseline**: Statistical fingerprint of an identity's writing style, built from initial output samples
- **Rolling Window**: A sliding window of recent outputs used to compute current style metrics
- **Drift Score**: Z-score measuring how far current outputs deviate from the baseline
- **Drift Alert**: Fired when the drift score exceeds the configured threshold

## Features

- **Event-Driven Analysis**: Subscribes to LLM output events — no polling needed
- **Per-Identity Baselines**: Separate statistical profiles for each identity
- **Rolling Window Detection**: Compares recent outputs against established baseline
- **Z-Score Alerting**: Configurable sensitivity threshold (default: 2.0 standard deviations)
- **Security Detection**: Catches identity corruption from prompt injection
- **Quality Monitoring**: Detects personality flattening over long runtimes
- **Pure Analysis**: No LLM calls — read-only access to output data

## Architecture

```
LLM Output Events
      │
      ▼
  Output Buffer ──▶ Stylometric Analysis
                          │
                    ┌─────▼──────┐
                    │  Baseline?  │
                    └──┬─────┬───┘
                   No  │     │ Yes
                       ▼     ▼
              Build       Compute
              Baseline    Drift Score
                              │
                         Threshold?
                         ┌────┴────┐
                     Below      Above
                       │          │
                     (ok)     Fire Alert
```

## Setup

### Configuration

Add to personality YAML:

```yaml
compass:
  window_size: 20        # Samples in rolling window (default: 20)
  baseline_samples: 10   # Samples needed for baseline (default: 10)
  drift_threshold: 2.0   # Z-score alert threshold (default: 2.0)
```

### Activation

The plugin activates when `compass` configuration is present in the personality YAML, or when explicitly enabled in the plugin list.

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main CompassPlugin class with event-driven drift detection |
| `stylometry.py` | Text analysis functions: sentence length, vocabulary, punctuation |
| `models.py` | Data models: BaselineProfile, DriftMetrics, DriftAlert, StyleMetrics |
| `__init__.py` | Package init |

## Testing

```bash
pytest tests/plugins/compass/ -v
```

## Security

Compass is a pure analysis tool. It makes no LLM calls and has read-only access to output data. It cannot modify identity behavior — it only reports deviations.
