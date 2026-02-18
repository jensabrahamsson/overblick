# Email Agent Plugin

Agentic email classification and action plugin for the Överblick framework. Uses LLM-driven decision making with goals, state, learnings, and reinforcement via principal feedback.

## Overview

Unlike a classic email filter (hard-coded if/else routing), the Email Agent uses an LLM to classify incoming emails and decide actions autonomously. It maintains goals, accumulates learnings from supervisor and principal feedback, and improves its classification accuracy over time through a closed feedback loop.

The plugin is designed around the **Stål** personality — an experienced executive secretary with decades of diplomatic and corporate communication experience. Stål is the natural match for this plugin because the role requires precisely what Stål embodies: multilingual precision, professional judgment, cultural sensitivity, and Swiss-banker discretion. See the [Stål personality README](../../personalities/stal/README.md) for the full character profile.

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The Email Agent is a **functional plugin** that classifies emails via LLM and executes actions (ignore, notify, reply, ask boss).

**How Email Agent fits in**: This plugin orchestrates several capabilities: `gmail` (fetch/send email), `telegram_notifier` (tracked notifications with feedback loop), and `boss_request` (supervisor research via IPC). The identity's personality drives reply tone and classification judgment. Designed around the Stal identity but usable by any identity with appropriate capabilities.

## Features

- **LLM-Driven Classification**: Every email is classified by the LLM with confidence scoring — no hard-coded rules
- **Multilingual Replies**: Mirrors the sender's language (Swedish, English, German, French)
- **Smart Sender Filtering**: Opt-in/opt-out applies only to REPLY; NOTIFY works for all senders
- **Supervisor Research**: Can request web research from the supervisor via IPC for informed replies
- **Telegram Notifications**: Tracked notifications with feedback loop for reinforcement learning
- **Feedback Loop**: Principal can reply to Telegram notifications; feedback is classified and stored as learning
- **Boss Consultation**: Low-confidence classifications are escalated to the supervisor via IPC
- **GDPR Compliance**: 30-day retention for email content, automatic purging on startup
- **Sender Profiles**: GDPR-safe aggregate-only profiles (counts, language preference, intent distribution)
- **Goal System**: Tracked goals with priorities that guide classification behavior

## Architecture

```
                                 ┌─────────────────┐
                                 │   Gmail IMAP     │
                                 │   (fetch unread) │
                                 └────────┬────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ tick()                                                           │
│                                                                  │
│  1. FETCH unread emails via GmailCapability                     │
│  2. For each email:                                             │
│     ├─ Wrap in boundary markers (security)                      │
│     ├─ CLASSIFY via LLM (→ intent + confidence)                 │
│     ├─ Confidence check (< 0.7 → ASK_BOSS)                     │
│     ├─ Record in DB (get record_id)                             │
│     ├─ EXECUTE action:                                          │
│     │   ├─ IGNORE → log and skip                                │
│     │   ├─ NOTIFY → TG notification (tracked)                   │
│     │   ├─ REPLY  → sender check → reply or fallback to NOTIFY  │
│     │   └─ ASK_BOSS → IPC to supervisor                         │
│     └─ Update sender profile                                    │
│  3. CHECK Telegram feedback (feedback loop)                     │
│     ├─ fetch_updates() from TG                                  │
│     ├─ Match replies to tracked notifications                   │
│     ├─ Classify feedback (positive/negative/neutral)            │
│     ├─ Store as AgentLearning                                   │
│     └─ Optionally acknowledge                                   │
└──────────────────────────────────────────────────────────────────┘
                     │                              │
          ┌──────────┘                    ┌─────────┘
          ▼                               ▼
┌─────────────────┐            ┌─────────────────────┐
│ Supervisor IPC  │            │ BossRequestCapability│
│ (ask_boss /     │            │ (web research via    │
│  consultation)  │            │  DuckDuckGo + LLM)   │
└─────────────────┘            └─────────────────────┘
```

## Intents

| Intent | Action | Sender Filter | Example |
|--------|--------|---------------|---------|
| `IGNORE` | Log and skip | All senders | Spam, newsletters, automated notifications |
| `NOTIFY` | Telegram notification (tracked) | All senders | Important email needing human review |
| `REPLY` | Generate and send email reply | Allowed senders only* | Meeting requests, project questions |
| `ASK_BOSS` | IPC to supervisor for guidance | All senders | Uncertain classification (confidence < 0.7) |

*If REPLY is classified for a non-allowed sender, the plugin falls back to NOTIFY (the email is important enough to warrant a reply, so the principal should at least be notified).

## Setup

### Prerequisites

1. **Gmail IMAP/SMTP**: Configure Gmail credentials in secrets
2. **Telegram Bot**: Create a bot via @BotFather for notifications
3. **Supervisor** (optional): Required for ASK_BOSS and research capabilities
4. **LLM**: Requires Ollama with `qwen3:8b` (or equivalent model)

### Secrets

Add to `config/stal/secrets.yaml`:

```yaml
# Required
principal_name: "Your Name"            # Name injected into prompts (never hardcoded)
gmail_address: "user@gmail.com"
gmail_app_password: "xxxx xxxx xxxx"    # Gmail App Password (not regular password)

# For Telegram notifications
telegram_bot_token: "123456:ABC-DEF..."
telegram_chat_id: "12345678"           # Private chat ID (same as owner ID for DMs)
telegram_owner_id: "12345678"          # Owner's Telegram user ID — only this user
                                       # can communicate with the bot
```

**Finding your Telegram user ID:** Send a message to `@userinfobot` on Telegram — it replies with your numeric user ID. The `telegram_owner_id` ensures the bot only processes messages from the instance owner, even if the bot token is somehow exposed.

### Personality Configuration

The email agent config lives in `personalities/stal/personality.yaml`:

```yaml
# Sender filtering — opt-in or opt-out mode
email_agent:
  filter_mode: "opt_in"               # "opt_in" or "opt_out"
  allowed_senders:                     # Used when filter_mode = opt_in
    - "colleague@example.com"
    - "boss@company.com"
  blocked_senders: []                  # Used when filter_mode = opt_out

# Capabilities (all required for full functionality)
capabilities:
  - "email_agent"           # This plugin
  - "gmail"                 # Email fetch + send
  - "telegram_notifier"     # Notifications + feedback
  - "boss_request"          # Research via supervisor
```

### Filter Modes

**opt_in** (recommended): Only senders in `allowed_senders` receive automatic replies. All other senders are still classified and can trigger NOTIFY or ASK_BOSS, but never receive an automated reply.

**opt_out**: All senders receive replies except those in `blocked_senders`. More permissive — use with caution.

### Running

```bash
# Start Stål with the email agent
python -m overblick run stal

# The agent will:
# 1. Poll Gmail every 5 minutes (configurable)
# 2. Classify each email via LLM
# 3. Execute appropriate action
# 4. Check for Telegram feedback
# 5. Learn from feedback
```

## Smart Sender Filtering

The sender filter is intentionally applied **only to REPLY**, not to classification:

```
ALL emails → classified by LLM → intent decided
                                      │
                          ┌───────────┼───────────┐───────────┐
                          ▼           ▼           ▼           ▼
                       IGNORE      NOTIFY       REPLY      ASK_BOSS
                     (all senders) (all senders) │       (all senders)
                                                 │
                                          ┌──────┴──────┐
                                          ▼             ▼
                                    Allowed?        Not allowed?
                                    → Send reply    → Fallback to NOTIFY
```

**Why?** A non-allowed sender's email may still be important. By classifying everything and only restricting who gets automated replies, the principal stays informed about all significant incoming email via Telegram notifications.

## Research via Supervisor

When the email agent needs factual information to compose a reply, it can request research through the `BossRequestCapability`:

1. Stål sends a `research_request` via IPC to the supervisor
2. The supervisor's `ResearchHandler` queries DuckDuckGo Instant Answer API
3. Results are summarized by the supervisor's LLM (Anomal's personality)
4. The summary is returned to Stål and incorporated into the reply

This keeps Stål focused on email while leveraging the supervisor for web access.

## Telegram Feedback Loop

After sending a notification, the principal can reply directly in Telegram:

```
Stål → TG: "*Email from finance@acme.com*
           _Q4 Financial Report_
           Important financial update requiring your review."

Principal → TG (reply): "Bra att du flaggade det!"

Stål → classifies feedback as POSITIVE
     → stores as AgentLearning (source: "principal_feedback")
     → refreshes learnings cache
     → optionally acknowledges: "Noted, I'll adjust my classification accordingly."
```

Feedback is classified as:
- **Positive** ("bra", "tack", "great") — reinforces the classification
- **Negative** ("inte viktigt", "sluta", "spam") — corrects future behavior
- **Neutral** — no learning stored

The classification uses LLM when available, with a heuristic keyword fallback.

## Database

Uses SQLite via the framework's `DatabaseBackend`. Four tables managed by versioned migrations:

| Table | Version | Purpose |
|-------|---------|---------|
| `email_records` | v1 | Classification history (intent, confidence, reasoning, feedback) |
| `agent_learnings` | v2 | Accumulated learnings from boss and principal feedback |
| `agent_goals` | v3 | Tracked goals with priority and progress |
| `notification_tracking` | v4 | Links TG notifications to email records for feedback loop |

### GDPR Compliance

- Email content (`email_snippet`, `reasoning`, `boss_feedback`) is purged after 30 days
- Classification metadata (intent, confidence, sender address) is retained for aggregate stats
- Sender profiles store only aggregate data (interaction count, language preference, intent distribution)
- Purging runs automatically on plugin startup

## Dependencies

| Capability | Purpose | Required? |
|------------|---------|-----------|
| `gmail` | Fetch unread emails, send replies | Yes |
| `telegram_notifier` | Send tracked notifications, receive feedback | Yes (for NOTIFY) |
| `boss_request` | Request research from supervisor | No (degrades gracefully) |
| `SafeLLMPipeline` | All LLM calls (classification, reply gen, feedback) | Yes |
| `IPC` | Boss consultation, research requests | No (degrades gracefully) |

## Testing

```bash
# All email agent tests
./venv/bin/python3 -m pytest tests/plugins/email_agent/ -v

# Scenario tests (multilingual, sender filtering, feedback)
./venv/bin/python3 -m pytest tests/plugins/email_agent/test_scenarios.py -v

# Unit tests (classification, prompts, database)
./venv/bin/python3 -m pytest tests/plugins/email_agent/test_email_agent.py -v

# Related capability tests
./venv/bin/python3 -m pytest tests/capabilities/test_telegram_notifier.py -v
./venv/bin/python3 -m pytest tests/capabilities/test_boss_request.py -v
./venv/bin/python3 -m pytest tests/supervisor/test_research_handler.py -v

# Full suite (excluding LLM tests)
./venv/bin/python3 -m pytest tests/ -v -m "not llm" -x
```

### Test Coverage

- Classification pipeline (JSON parsing, confidence thresholds, error handling)
- All four intents (IGNORE, NOTIFY, REPLY, ASK_BOSS)
- Smart sender filtering (allowed reply, blocked fallback to notify)
- Research integration (with and without research context)
- Telegram feedback loop (positive, negative, neutral, LLM failure fallback)
- Notification tracking (DB storage, lookup by TG message ID)
- Database operations (CRUD, GDPR purge, migrations)
- Multilingual scenarios (English, Swedish, German, French)
- Boss consultation (IPC send, response processing, no-IPC fallback)
- Prompt structure (all prompt functions, English enforcement)

## Security

### Input Sanitization

All external email content is wrapped in boundary markers before reaching the LLM:

```python
safe_subject = wrap_external_content(subject, "email_subject")
safe_body = wrap_external_content(body[:3000], "email_body")
```

This prevents prompt injection from malicious email content.

### SafeLLMPipeline

All LLM calls go through SafeLLMPipeline:
- Preflight checks (skipped for system-generated content like classification prompts)
- Output safety validation
- Audit logging
- Rate limiting

### Secrets

- `principal_name` loaded from secrets at runtime — never hardcoded in code or personality YAML
- Gmail credentials stored in encrypted `secrets.yaml`
- Telegram bot token stored in encrypted `secrets.yaml`
- All secrets accessed via `ctx.get_secret()`

### English Enforcement

All internal agent-to-agent communication uses English:
- Boss consultation prompts include explicit English enforcement
- Supervisor email handler responds in English
- Research requests and responses use English
- Only external-facing replies mirror the sender's language

## Troubleshooting

### No Emails Being Processed

1. Check Gmail capability is configured (`gmail` in capabilities list)
2. Verify Gmail credentials in secrets
3. Check logs for `EmailAgent: gmail capability not available`
4. Ensure quiet hours are not active (default: 22:00-06:00 Stockholm)

### All Emails Classified as ASK_BOSS

1. Check confidence threshold (default: 0.7)
2. Review recent learnings — conflicting feedback can reduce confidence
3. Check LLM model availability (requires `qwen3:8b`)
4. Review classification prompt in `prompts.py`

### Notifications Not Arriving

1. Verify Telegram bot token and chat ID in secrets
2. Check `telegram_notifier` is in capabilities list
3. Look for `telegram_notifier capability not available` in logs
4. Test bot directly: `curl https://api.telegram.org/bot<TOKEN>/getMe`

### Feedback Not Being Processed

1. Ensure `fetch_updates()` is working (check TG bot API)
2. Verify the principal is replying to (not just reacting to) the notification
3. Check that the notification was tracked in `notification_tracking` table
4. Look for `Processed TG feedback` log entries

### Replies Not Sent to Certain Senders

This is by design. Check `filter_mode` and `allowed_senders` in personality config. If `filter_mode` is `opt_in`, only senders in the allowed list receive replies. Others get NOTIFY instead.

### Research Not Working

1. Verify `boss_request` capability is in the capabilities list
2. Check that the supervisor is running with IPC enabled
3. Look for `boss_request capability not available` in logs
4. Research requires internet access on the supervisor side (DuckDuckGo API)
