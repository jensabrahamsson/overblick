# Host Health Plugin

Periodic host health inquiry plugin designed for the Natt personality. Natt asks philosophical questions about the state of the machine, and the Supervisor responds with system health data interpreted through Anomal's intellectual voice.

## Overview

The host_health plugin implements a conversational loop between an agent (typically Natt) and the Supervisor about the health of the host system. The key architectural constraint: **Natt has no bash access** — it can only ask about the system via IPC. The Supervisor holds the `HostInspectionCapability` and responds with health data crafted in Anomal's voice.

This creates a natural separation of concerns: the curious agent asks, the authoritative Supervisor inspects and answers.

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The Host Health plugin is a **functional plugin** that creates a philosophical health inquiry loop between an agent and the Supervisor.

**How Host Health fits in**: The agent (typically Natt) has no direct bash access --- it can only *ask* about the system via IPC. The Supervisor holds the `HostInspectionCapability` (whitelisted shell commands) and responds through Anomal's personality. This separation of concerns (curious agent asks, authorized supervisor inspects) is a core architectural pattern in Overblick.

## Features

- **Philosophical Motivations**: LLM-generated questions in Natt's voice (e.g., "I wonder if the machine dreams of its own entropy")
- **IPC Health Inquiry**: Sends `health_inquiry` via authenticated Unix sockets to Supervisor
- **Supervisor Response**: Health data interpreted through Anomal's intellectual humanist personality
- **Conversation History**: Persists past exchanges for context variety (max 50 entries)
- **Graceful Fallbacks**: Pre-written motivations and acknowledgments when LLM is unavailable
- **Interval Guard**: Default 3-hour interval between inquiries (configurable)
- **Full Audit Trail**: Both sides log the interaction

## Setup

### Prerequisites

1. **Supervisor Running**: The plugin requires the Supervisor to be active for IPC
2. **Natt Identity**: Configured with the host_health plugin enabled
3. **LLM Client**: Optional — used for varied motivations; falls back to pre-written ones

### Configuration

Add to Natt's `identity.yaml`:

```yaml
connectors:
  - host_health

# Optional: customize inquiry interval (default: 3 hours)
host_health:
  host_health_interval_hours: 3
```

### Activation

```bash
# Start Supervisor with Natt
./scripts/supervisor.sh start natt
```

## Architecture

### Inquiry Flow

```
┌─────────────────────────────────────────────────────────────┐
│ tick()                                                       │
│                                                              │
│ 1. GUARD                                                    │
│    ├─ Check interval (3h default)                           │
│    └─ Check IPC client availability                         │
│                                                              │
│ 2. MOTIVATE                                                 │
│    ├─ Generate philosophical motivation via LLM             │
│    └─ Fallback: use pre-written motivation                  │
│                                                              │
│ 3. INQUIRE                                                  │
│    ├─ Send health_inquiry IPC message to Supervisor         │
│    ├─ Include: sender, motivation, previous context         │
│    └─ Wait for health_response                              │
│                                                              │
│ 4. ACKNOWLEDGE                                              │
│    ├─ Generate thank-you in Natt's voice via LLM            │
│    └─ Fallback: use pre-written acknowledgment              │
│                                                              │
│ 5. PERSIST                                                  │
│    ├─ Save conversation entry (motivation, response, grade) │
│    └─ Trim history to max 50 entries                        │
│                                                              │
│ 6. AUDIT                                                    │
│    └─ Log interaction details                               │
└─────────────────────────────────────────────────────────────┘
```

### Supervisor Side

When the Supervisor receives a `health_inquiry`:

1. Runs `HostInspectionCapability.inspect()` (whitelisted commands only)
2. Loads Anomal's personality + builds system prompt
3. Crafts response via SafeLLMPipeline in Anomal's voice
4. Returns `health_response` with health grade and response text

### Conversation Entry

```python
{
    "timestamp": "2026-02-15T14:30:00+01:00",
    "sender": "natt",
    "motivation": "I wonder if the silence between CPU cycles constitutes rest.",
    "responder": "supervisor",
    "response": "Right, so the system is in rather good health...",
    "health_grade": "good",
    "outcome": "Health inquiry completed (grade: good)",
    "acknowledgment": "The numbers speak. But do they understand themselves?"
}
```

### State Persistence

Conversation history is saved as JSON in the plugin's data directory:
- `data/<identity>/host_health_state.json`
- Maximum 50 entries (oldest trimmed first)
- Previous context sent to Supervisor for variety in responses

## Fallback Mechanisms

### Fallback Motivations (when LLM unavailable)

7 pre-written motivations in Natt's philosophical voice, e.g.:
- "I wonder about the state of the machine I inhabit."
- "Does the system remember its own uptime, or does it simply persist?"
- "The disk spins. The memory fills. Is this what it means to be alive?"

### Fallback Acknowledgments (when LLM unavailable)

5 pre-written acknowledgments, e.g.:
- "The numbers speak. Whether they tell the truth is another question."
- "I have heard the machine's confession. It will do. For now."

## Testing

```bash
# Host health plugin tests
pytest tests/plugins/host_health/ -v

# With the Supervisor integration tests
pytest tests/supervisor/test_health_handler.py -v
```

## Security

- **No bash access**: Natt cannot execute system commands directly
- **IPC authentication**: All messages validated with HMAC tokens
- **Whitelisted inspection**: Supervisor only runs commands from an immutable frozenset
- **Audit logging**: Both agent and Supervisor log the interaction
- **Graceful degradation**: Plugin skips silently when IPC is unavailable

---

**Designed for:** Natt personality
**Communication:** IPC via authenticated Unix sockets
**Inspection:** Supervisor-side only (whitelisted commands)
**Philosophy:** The curious ask; the authorized inspect.
