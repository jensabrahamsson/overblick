# Communication Capabilities

## Overview

The **communication** bundle provides inter-system messaging capabilities for agent plugins. It enables agents to send emails (SMTP/IMAP), receive Telegram notifications with feedback loops, and request web research from the Supervisor via IPC.

This bundle powers Stål's email agent workflow and any plugin that needs to communicate with the outside world or escalate decisions to the Supervisor.

## Capabilities

### EmailCapability

SMTP email sending with TLS/SSL support. Works with any SMTP provider (Gmail, Brevo, SendGrid, etc.).

**Registry name:** `email`

### GmailCapability

Full Gmail integration via IMAP (read) and SMTP (send) using Google App Passwords. Supports email threading with proper `In-Reply-To` and `References` headers so Gmail groups replies correctly.

**Registry name:** `gmail`

### TelegramNotifierCapability

Telegram Bot API integration for sending notifications and receiving user feedback. Supports tracked notifications (correlates Telegram message IDs with email records) and owner-filtered message polling.

**Registry name:** `telegram_notifier`

### BossRequestCapability

IPC bridge to the Supervisor for web research requests. Agents ask the boss for information via authenticated Unix sockets; the Supervisor performs DuckDuckGo searches and returns LLM-summarized results.

**Registry name:** `boss_request`

## Methods

### EmailCapability

```python
async def send(
    self,
    to: str,
    subject: str,
    body: str,
    html: bool = False,
    from_email: str | None = None,
) -> bool:
    """
    Send an email via SMTP.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text or HTML).
        html: If True, send as HTML email.
        from_email: Optional override for From address.

    Returns:
        True if sent successfully, False otherwise.
    """
```

**Required secrets:** `smtp_server`, `smtp_port`, `smtp_login`, `smtp_password`, `smtp_from_email`

### GmailCapability

```python
async def fetch_unread(
    self,
    max_results: int = 10,
    since_days: int | None = None,
) -> list[GmailMessage]:
    """Fetch unread emails from IMAP inbox, newest first.

    Args:
        max_results: Maximum number of emails to return.
        since_days: If set, uses IMAP SINCE filter to only fetch
                    emails from the last N days (server-side filtering).
    """

async def send_reply(
    self,
    thread_id: str,
    message_id: str,
    to: str,
    subject: str,
    body: str,
) -> bool:
    """Send a reply with proper threading headers."""

async def mark_as_read(self, message_id: str) -> bool:
    """Mark an email as read via IMAP UID."""
```

**Required secrets:** `gmail_address`, `gmail_app_password`
**Optional secrets:** `gmail_send_as` (Gmail alias for "Send mail as")

**Setup:**
1. Enable 2-step verification on your Google Account
2. Create an App Password: Google Account → Security → App Passwords
3. Store credentials: `python scripts/setup_gmail.py --identity <name>`

### TelegramNotifierCapability

```python
async def send_notification(self, message: str) -> bool:
    """Send a Markdown-formatted notification."""

async def send_notification_tracked(
    self, message: str, ref_id: str = "",
) -> int | None:
    """Send a tracked notification. Returns Telegram message_id."""

async def send_html(self, message: str) -> bool:
    """Send an HTML-formatted notification."""

async def fetch_updates(self, limit: int = 10) -> list[TelegramUpdate]:
    """Poll for new messages (offset-based, avoids re-processing)."""
```

**Required secrets:** `telegram_bot_token`, `telegram_chat_id`
**Optional secrets:** `telegram_owner_id` (restricts who the bot accepts messages from)

**TelegramUpdate model:**
```python
{
    "message_id": int,
    "text": str,
    "reply_to_message_id": int | None,
    "timestamp": str,
}
```

### BossRequestCapability

```python
async def request_research(
    self, query: str, context: str = "",
) -> str | None:
    """
    Ask the Supervisor for web research.

    Args:
        query: Search query.
        context: Additional context for the research.

    Returns:
        Concise English summary, or None on failure/timeout.
    """
```

**Timeout:** 60s (research involves web search + LLM summarization)

**Graceful degradation:** If no IPC client is available, `configured=False` and calls return None.

## Plugin Integration

Plugins access communication capabilities through `PluginContext`:

```python
class MyPlugin(PluginBase):
    async def setup(self) -> None:
        self.gmail = self.ctx.get_capability("gmail")
        self.telegram = self.ctx.get_capability("telegram_notifier")
        self.boss = self.ctx.get_capability("boss_request")

    async def handle_email(self, email: dict):
        # Send a reply via Gmail
        await self.gmail.send_reply(
            to=email["sender"],
            subject=f"Re: {email['subject']}",
            body="Thank you for your message.",
            thread_id=email["thread_id"],
            message_id=email["message_id"],
        )

        # Notify on Telegram
        await self.telegram.send_notification(
            f"Replied to email from {email['sender']}"
        )

        # Ask the boss for research
        if self.boss and self.boss.configured:
            info = await self.boss.request_research(
                "What is the current EUR/SEK rate?"
            )
```

## Configuration

Configure communication capabilities in your personality's `personality.yaml`:

```yaml
capabilities:
  - communication  # Expands to: email, gmail, telegram_notifier, boss_request
```

Or load individual capabilities:

```yaml
capabilities:
  telegram_notifier:
    # Config passed to capability setup
  gmail:
    # Config passed to capability setup
```

Secrets are loaded per-identity from `config/secrets/<identity>.yaml` via `ctx.get_secret()`.

## Security

### Email (SMTP/IMAP)
- All connections use TLS/STARTTLS (port 465 → SSL, port 587 → STARTTLS)
- IMAP connections use SSL (port 993)
- Credentials stored in encrypted secrets, never logged
- 30s connection timeout
- Audit logging of all sent emails (to, subject, length)

### Telegram
- HTTPS-only (Telegram API enforces TLS)
- Bot token never logged
- Owner filtering: only accepts messages from configured `telegram_owner_id`
- Chat filtering: only processes messages from configured `telegram_chat_id`

### IPC (Boss Requests)
- Authenticated Unix sockets with HMAC token validation
- Socket permissions: owner-only (0o600)
- 60s timeout prevents hanging
- Falls back gracefully when IPC unavailable

## Testing

```bash
# All communication capability tests
pytest tests/capabilities/communication/ -v

# Individual capability tests
pytest tests/capabilities/communication/test_email.py -v
pytest tests/capabilities/communication/test_gmail.py -v
pytest tests/capabilities/communication/test_telegram_notifier.py -v
pytest tests/capabilities/communication/test_boss_request.py -v
```

## Related Bundles

- **content** — Text summarization
- **monitoring** — Host system inspection
- **engagement** — Content analysis and response generation
- **knowledge** — Safe learning and knowledge loading
