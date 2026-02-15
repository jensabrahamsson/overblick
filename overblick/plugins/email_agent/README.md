# Email Agent Plugin

Prompt-driven email classification and action plugin for the Overblick framework.

## Overview

Unlike the classic GmailPlugin (hard-coded if/else routing), the Email Agent uses LLM to classify incoming emails and decide actions. It has goals, state, learnings, and reinforcement via boss agent feedback.

## Architecture

```
Email → Classification Prompt → LLM → Intent Decision → Action
                                  ↑                        ↓
                              Learnings              Database Record
                              (from boss             (for history)
                               feedback)
```

## Intents

| Intent | Action | Example |
|--------|--------|---------|
| `IGNORE` | Log and skip | Spam, newsletters, automated notifications |
| `NOTIFY` | Telegram notification to principal | Important but needs human review |
| `REPLY` | Generate and send email reply | Meeting requests, project questions |
| `ASK_BOSS` | IPC to supervisor for guidance | Uncertain classification (low confidence) |

## Personality: Stål

The email agent uses the **Stål** personality — an experienced executive secretary who:
- Always responds in the language of the incoming email
- Signs replies as "Stål / Digital Assistant to {principal_name}" (transparent about being a digital assistant)
- Is formal, precise, and professional
- The principal's name is injected from secrets (`principal_name` key), never hardcoded

## Configuration

In `personality.yaml`:

```yaml
email_agent:
  filter_mode: "opt_in"          # or "opt_out"
  allowed_senders:               # opt_in mode
    - "colleague@example.com"
  blocked_senders: []            # opt_out mode
```

## Database

Uses SQLite via the framework's `DatabaseBackend`. Tables:
- `email_records` — classification history
- `agent_learnings` — learnings from boss feedback
- `agent_goals` — tracked goals

## Dependencies

- `overblick.core.llm.pipeline` — SafeLLMPipeline for all LLM calls
- `overblick.supervisor.ipc` — IPC for boss consultations
- `overblick.capabilities.communication.telegram_notifier` — Telegram notifications
- `overblick.plugins.gmail` — Email sending via event bus
