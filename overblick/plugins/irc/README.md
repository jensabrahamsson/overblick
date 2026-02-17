# IRC Plugin

Identity-to-identity conversations between agent personalities.

## Purpose

The IRC plugin orchestrates free-form conversations between Överblick identities on curated topics. It runs at low system load, using the SafeLLMPipeline for each turn, and stores conversations as JSON for the dashboard to display.

Unlike external-facing plugins (Telegram, Moltbook), IRC is **internal only** — identities talk to each other, creating organic cross-personality discussions.

## Architecture

```
TopicManager         → Selects topic, scores identity interest
  ↓
select_participants  → Picks 2+ identities based on interest scores
  ↓
IRCPlugin._run_turns → Turn Coordinator manages conversation flow
  ↓                    LoadGuard checks system health before each turn
ConversationStorage  → JSON files in data/<identity>/irc/
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `IRCPlugin` | `plugin.py` | Main plugin — lifecycle, turn coordination, LLM calls |
| `TopicManager` | `topic_manager.py` | Topic selection and participant scoring |
| `Models` | `models.py` | `IRCConversation`, `IRCTurn`, `ConversationState` |

## How It Works

1. **Tick** — The scheduler calls `tick()` every 5 minutes
2. **Load Guard** — Checks CPU (<50%) and memory (<80%) before proceeding
3. **Quiet Hours** — Respects identity quiet hours configuration
4. **Topic Selection** — Picks an unused topic, scores identity interest
5. **Participant Selection** — Chooses 2+ identities with highest interest
6. **Turn Execution** — Round-robin speaker selection, each turn goes through SafeLLMPipeline
7. **Storage** — Conversations saved as JSON after each turn
8. **Events** — Emits `irc.conversation_start`, `irc.new_turn`, `irc.conversation_end`

## Configuration

The IRC plugin is configured per-identity in `identity.yaml`:

```yaml
connectors:
  - irc

schedule:
  feed_poll_minutes: 5  # IRC tick interval
```

### Conversation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max turns per conversation | `min(20, participants * 5)` | Prevents runaway conversations |
| Max stored conversations | 50 | Older conversations are pruned |
| Turn interval | 5 seconds | Pause between LLM calls |
| System load threshold | CPU <50%, Memory <80% | Conversations pause under high load |

## Dependencies

- **SafeLLMPipeline** — All turns go through the security pipeline
- **EventBus** — Conversation lifecycle events
- **Identity System** — `load_identity()` and `build_system_prompt()` for each participant
- **HostInspectionCapability** — System load monitoring (optional, degrades gracefully)

## Dashboard Integration

The IRC dashboard tab (`/irc`) displays stored conversations with:
- Topic and participant list
- Turn-by-turn conversation view
- Conversation state (active, paused, completed, cancelled)

## Testing

```bash
# Run IRC plugin tests
./venv/bin/python3 -m pytest tests/plugins/irc/ -v
```
