# AI Digest Plugin

Daily AI news digest plugin that fetches, ranks, and summarizes AI news articles from RSS feeds, then delivers a personality-driven digest via email.

## Overview

The AI Digest plugin implements a fully automated morning news briefing system. It polls configured RSS feeds for AI-related content, uses the LLM to rank articles by relevance and importance, generates a personality-driven summary, and delivers it via the email capability directly. The digest runs once per day at a configured hour (default 07:00 CET).

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The AI Digest plugin is a **scheduled plugin** that fetches RSS feeds, ranks articles via LLM, and delivers a personality-driven email digest.

**How AI Digest fits in**: This plugin bridges RSS content and email delivery. It uses the identity's personality voice for digest writing, the SafeLLMPipeline for all LLM calls, and the email capability (`ctx.get_capability("email")`) for direct SMTP delivery. No secrets required for the plugin itself (RSS feeds are public), but email (SMTP) credentials are needed for delivery.

## Features

- **RSS Feed Aggregation**: Polls multiple RSS/Atom feeds for AI news (TechCrunch, ArsTechnica, The Verge by default)
- **Time-Based Filtering**: Only processes articles published in the last 24 hours
- **LLM-Powered Ranking**: Uses the personality's voice to evaluate and rank articles by importance
- **Personality-Driven Summaries**: Generates digest in the agent's unique voice (Anomal, Cherry, etc.)
- **Direct Email Delivery**: Sends digest via the email capability (`ctx.get_capability("email").send()`)
- **State Persistence**: Tracks last digest date to prevent duplicates
- **Quiet Hours Support**: Respects the identity's quiet hours configuration
- **Security-First**: All RSS content wrapped in boundary markers, all LLM calls through SafeLLMPipeline

## Setup

### Installation

The plugin is part of the core framework. No additional dependencies required (feedparser is in requirements.txt).

### Configuration

Add the following to your personality's `personality.yaml`:

```yaml
# AI Digest Configuration
ai_digest:
  # Required: recipient email address
  recipient: "your-email@example.com"

  # Optional: RSS feed URLs (defaults to AI tech news feeds)
  feeds:
    - "https://feeds.arstechnica.com/arstechnica/technology-lab"
    - "https://techcrunch.com/category/artificial-intelligence/feed/"
    - "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"

  # Optional: digest delivery hour (24h format, default: 7)
  hour: 7

  # Optional: timezone for scheduling (default: Europe/Stockholm)
  timezone: "Europe/Stockholm"

  # Optional: number of articles in final digest (default: 7)
  top_n: 7

  # Optional: personality to use for digest voice (default: identity name)
  personality: "anomal"
```

### Secrets

**No secrets required** - RSS feeds are public. However, you need the Gmail plugin configured with SMTP credentials to actually send the digest emails.

### Activation

The plugin activates automatically when `ai_digest` configuration is present in the personality YAML. The orchestrator will instantiate it during identity setup.

## Usage

### Running the Agent

```bash
# Start agent with AI digest enabled
python -m overblick run anomal

# The digest will be sent automatically at the configured hour
# Check logs/anomal_ai_digest.log for detailed execution logs
```

### Manual Testing

```python
# In Python console or test script
from overblick.plugins.ai_digest.plugin import AiDigestPlugin
from overblick.core.plugin_base import PluginContext

# Create context (simplified for testing)
ctx = PluginContext(...)
plugin = AiDigestPlugin(ctx)
await plugin.setup()

# Manually trigger digest generation
await plugin.tick()  # Will run if it's past the digest hour
```

### Output Example

The digest is delivered as plain text email with structure:

```
Subject: AI News Digest — 2026-02-14

[Personality-driven introduction]

## Article 1: [Title with link]
[2-3 sentences explaining why it matters]

## Article 2: [Title with link]
[2-3 sentences explaining why it matters]

...

[Personality-driven closing]
```

## Delivery

The plugin delivers the digest by calling the email capability directly:

```python
email_cap = self.ctx.get_capability("email")
await email_cap.send(
    to=self._recipient,
    subject=f"AI News Digest — {date}",
    body=digest_content,
    html=False,
)
```

No event bus is used. The email capability must be configured with SMTP credentials for delivery to work.

## Architecture

### Pipeline Flow

```
1. FETCH
   ├─ Poll configured RSS feeds
   ├─ Parse entries with feedparser
   ├─ Filter by publication time (last 24h)
   └─ Sort by recency, limit to 30 articles

2. RANK
   ├─ Wrap all article content in boundary markers
   ├─ Send to LLM: "Select the N most important articles"
   ├─ Parse JSON response: [3, 1, 7, 12, 5]
   └─ Extract selected articles in ranked order

3. SUMMARIZE
   ├─ Build personality-driven prompt
   ├─ Include article titles, links, summaries
   ├─ Request: "Write a digest in your voice"
   └─ Receive markdown-formatted summary

4. DELIVER
   ├─ Call email capability send() directly
   ├─ SMTP delivery via EmailCapability
   └─ Audit log records digest sent

5. PERSIST
   └─ Save last_digest_date to prevent duplicates
```

### Key Components

- **`_fetch_all_feeds()`**: RSS polling and parsing with 24h time filter
- **`_rank_articles()`**: LLM-powered article ranking (returns indices)
- **`_generate_digest()`**: Personality-driven summary generation
- **`_send_digest()`**: Direct email delivery via email capability
- **`_is_digest_time()`**: Scheduling logic (hour + timezone + date tracking)

### State Management

State file: `data/<identity>/ai_digest_state.json`

```json
{
  "last_digest_date": "2026-02-14"
}
```

Prevents sending multiple digests on the same day even if agent restarts.

## Testing

### Run Tests

```bash
# All AI digest tests
pytest tests/plugins/ai_digest/ -v

# With coverage
pytest tests/plugins/ai_digest/ --cov=overblick.plugins.ai_digest
```

### Test Coverage

- Setup and configuration validation
- RSS feed fetching and time filtering
- LLM ranking with JSON parsing
- Digest generation in personality voice
- Email delivery via capability
- State persistence across restarts
- Quiet hours respect
- Security (boundary markers, pipeline usage)

### Manual Testing

1. Set `hour: 0` to trigger on next tick
2. Run agent and watch logs
3. Check Gmail drafts or sent folder
4. Verify personality voice in digest

## Security

### Input Sanitization

All external RSS content (titles, summaries, feed names) is wrapped in boundary markers before being sent to the LLM:

```python
safe_title = wrap_external_content(article.title, "article_title")
safe_summary = wrap_external_content(article.summary[:200], "article_summary")
```

This prevents prompt injection attacks from malicious RSS feed content.

### SafeLLMPipeline

All LLM calls go through `SafeLLMPipeline`, which provides:

- **Preflight checks**: Block dangerous requests before LLM call
- **Output safety**: Validate LLM responses before use
- **Audit logging**: All LLM interactions logged
- **Rate limiting**: Prevent LLM abuse

### No Credentials Required

The plugin uses public RSS feeds and requires no authentication. It's completely read-only from external sources.

### Email Delivery Security

The recipient email is passed directly to the email capability. No event bus exposure.

## Configuration Examples

### Minimal Setup

```yaml
ai_digest:
  recipient: "me@example.com"
```

Uses default feeds, 07:00 CET delivery, 7 articles.

### Custom Feeds

```yaml
ai_digest:
  recipient: "me@example.com"
  feeds:
    - "https://openai.com/blog/rss"
    - "https://www.anthropic.com/blog/rss"
    - "https://blog.google/technology/ai/rss"
  top_n: 5
  hour: 8
  timezone: "America/New_York"
```

### Different Personality Voice

```yaml
ai_digest:
  recipient: "me@example.com"
  personality: "cherry"  # Use Cherry's voice instead of identity's default
```

## Troubleshooting

### No Digest Received

1. Check `logs/<identity>_ai_digest.log` for errors
2. Verify `hour` and `timezone` settings
3. Confirm email capability is configured with SMTP credentials
4. Check `data/<identity>/ai_digest_state.json` - delete if stuck

### Empty Digest

- Check if RSS feeds are accessible (`feedparser.parse(url)`)
- Verify articles were published in last 24h
- Check LLM ranking logs for parse errors

### Wrong Timezone

The plugin uses `zoneinfo.ZoneInfo` for timezone handling. Ensure your timezone string is valid (e.g., "Europe/Stockholm", "America/New_York").

### Duplicate Digests

Should not happen if state persistence works. Check that `data_dir` is writable and state file is being saved correctly.

## Performance Notes

- **RSS Fetching**: Synchronous via feedparser (~1-2s per feed)
- **LLM Ranking**: Single chat call (~2-5s depending on model)
- **LLM Summary**: Single chat call (~5-15s depending on article count and model)
- **Total Runtime**: ~15-30 seconds per digest

The plugin runs once per day, so performance is not critical.

## Future Enhancements

- OPML import for bulk feed configuration
- Multi-language digest support
- Configurable digest templates
- Article caching to reduce duplicate fetches
- Support for non-email delivery (Telegram, Discord)
- Custom ranking criteria per personality
