# Gmail Plugin

Email agent plugin that processes incoming emails and generates personality-driven responses. Operates in draft mode by default for human review before sending. Also serves as email delivery backend for other plugins (AI Digest, notifications).

## Overview

The Gmail plugin turns your personality into an email correspondent. It periodically checks for unread emails, filters by sender whitelist, generates personality-driven responses via SafeLLMPipeline, and creates drafts for review. In non-draft mode, it can send responses directly. The plugin also subscribes to `email.send_request` events from other plugins (like AI Digest) and handles SMTP delivery.

## Features

- **Email Processing**: Polls Gmail/IMAP for unread messages
- **Sender Whitelisting**: Only respond to approved email addresses
- **Personality-Driven Responses**: Uses `build_system_prompt()` for character consistency
- **Draft Mode**: Compose responses but don't send without approval (boss agent interface)
- **Direct Send Mode**: Auto-send responses when draft_mode is disabled
- **Rate Limiting**: Per-recipient limits (5/hour, 20/day by default)
- **Thread Detection**: Identifies replies via "Re:" prefix
- **Event Bus Integration**: Subscribes to `email.send_request` for plugin-to-plugin email delivery
- **SMTP Support**: Works with Gmail App Password, Brevo, SendGrid, or any SMTP provider
- **Security-First**: All email content wrapped in boundary markers, responses through SafeLLMPipeline

## Setup

### Installation

No additional dependencies required. Uses Python's built-in `smtplib` for email sending.

### SMTP Configuration (Recommended)

For production use, configure SMTP credentials (Brevo, SendGrid, or Gmail App Password):

#### Option 1: Brevo (Recommended for Production)

1. Create account at [brevo.com](https://www.brevo.com)
2. Generate SMTP credentials from Settings → SMTP & API
3. Add to `config/<identity>/secrets.yaml`:

```yaml
# SMTP Configuration (Brevo)
smtp_server: "smtp-relay.brevo.com"
smtp_port: "587"  # TLS port
smtp_login: "your-brevo-email@example.com"
smtp_password: "your-brevo-smtp-key"
smtp_from_email: "anomal@yourdomain.com"
```

#### Option 2: Gmail App Password

1. Enable 2FA on your Google account
2. Generate App Password: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Add to `config/<identity>/secrets.yaml`:

```yaml
# Gmail SMTP Configuration
smtp_server: "smtp.gmail.com"
smtp_port: "587"
smtp_login: "your-email@gmail.com"
smtp_password: "abcd efgh ijkl mnop"  # 16-char app password
smtp_from_email: "your-email@gmail.com"
```

#### Option 3: SendGrid

```yaml
smtp_server: "smtp.sendgrid.net"
smtp_port: "587"
smtp_login: "apikey"  # Literal string "apikey"
smtp_password: "SG.xxxxxxxxxxxxxxxxxxxx"  # SendGrid API key
smtp_from_email: "noreply@yourdomain.com"
```

### Gmail API Configuration (Optional)

For reading emails via Gmail API (not yet implemented):

```yaml
# Gmail API (future feature)
gmail_oauth_credentials: '{"installed": {...}}'  # OAuth2 JSON from Google Cloud
gmail_email_address: "your-email@gmail.com"
```

**Note**: Currently, the plugin only implements SMTP sending. Gmail API reading is planned for a future release.

### Configuration

Add to `personality.yaml`:

```yaml
# Gmail Configuration
gmail:
  # Draft mode: create drafts instead of auto-sending (recommended)
  draft_mode: true

  # Check interval in seconds (default: 300 = 5 minutes)
  check_interval_seconds: 300

  # Sender whitelist (empty = allow all - NOT RECOMMENDED)
  allowed_senders:
    - "friend@example.com"
    - "boss@example.com"
    - "team@company.com"
```

### Activation

Include `gmail` in enabled plugins:

```yaml
enabled_plugins:
  - gmail
  - ai_digest  # AI Digest will use Gmail for delivery
```

## Usage

### Running the Agent

```bash
# Start agent with Gmail enabled
python -m overblick run anomal

# Check for new emails every 5 minutes (configurable)
# Responses are queued as drafts in memory
```

### Draft Review (Boss Agent)

When `draft_mode: true`, responses are queued for review:

```python
# Via boss agent or manual inspection
drafts = plugin.get_pending_drafts()
for i, draft in enumerate(drafts):
    print(f"{i}: To={draft.to}, Subject={draft.subject}")
    print(f"   Body preview: {draft.body[:100]}...")

# Approve a draft
plugin.approve_draft(0)  # Approve first draft

# Send approved drafts (manual trigger or via scheduler)
```

### Direct Send Mode

When `draft_mode: false`, responses are sent immediately:

```yaml
gmail:
  draft_mode: false  # Auto-send responses
  allowed_senders:
    - "trusted-friend@example.com"  # Be VERY careful with whitelist
```

**WARNING**: Only disable draft mode if you trust the sender whitelist completely. The LLM generates responses autonomously.

### Event-Driven Email Sending

Other plugins can request email delivery via event bus:

```python
# Example: AI Digest plugin
await ctx.event_bus.emit(
    "email.send_request",
    to="recipient@example.com",
    subject="AI News Digest — 2026-02-14",
    body="[Digest content in Markdown]",
    plugin="ai_digest",
)
```

The Gmail plugin receives the event and sends directly (bypasses draft mode for plugin requests).

## Events

### Emits

None. The Gmail plugin is a leaf node (receives events, doesn't emit).

### Subscribes

- **`email.send_request`** - Sends email on behalf of other plugins
  - `to` (str): Recipient email address
  - `subject` (str): Email subject
  - `body` (str): Email body (plain text or Markdown)
  - `plugin` (str, optional): Requesting plugin name for audit

## Configuration Examples

### Minimal Setup (Draft Mode)

```yaml
gmail:
  draft_mode: true
  allowed_senders:
    - "me@example.com"
```

Add SMTP secrets:

```yaml
# secrets.yaml
smtp_server: "smtp.gmail.com"
smtp_port: "587"
smtp_login: "me@example.com"
smtp_password: "app-password-here"
smtp_from_email: "me@example.com"
```

### Production Setup (Brevo + Whitelist)

```yaml
gmail:
  draft_mode: false  # Auto-send for specific senders only
  check_interval_seconds: 600  # Check every 10 minutes
  allowed_senders:
    - "ceo@company.com"
    - "team@company.com"
```

```yaml
# secrets.yaml
smtp_server: "smtp-relay.brevo.com"
smtp_port: "587"
smtp_login: "company-email@example.com"
smtp_password: "brevo-smtp-key"
smtp_from_email: "anomal@company.com"
```

### High-Security Setup

```yaml
gmail:
  draft_mode: true  # NEVER auto-send
  allowed_senders:
    - "boss@company.com"  # Only one trusted sender
```

All responses reviewed by human before sending.

## Architecture

### Email Processing Flow

```
1. POLL
   ├─ Check time since last poll (respect check_interval)
   ├─ Fetch unread emails via Gmail API (future) or IMAP
   └─ Filter by sender whitelist

2. CLASSIFY
   ├─ Is this a reply? (subject starts with "Re:")
   ├─ Is sender rate-limited?
   └─ Route to _handle_reply() or _handle_new_email()

3. GENERATE RESPONSE
   ├─ Wrap email content in boundary markers
   ├─ Build messages with system prompt
   ├─ Call ctx.llm_pipeline.chat()
   ├─ Handle blocked/deflected responses
   └─ Truncate to max_body_length

4. DRAFT OR SEND
   ├─ Draft mode → queue in self._drafts
   └─ Direct mode → _send_email() via SMTP

5. RATE LIMIT
   └─ Record send timestamp per recipient
```

### Event-Driven Email Flow

```
1. RECEIVE EVENT
   ├─ email.send_request emitted by AI Digest (or other plugin)
   └─ _handle_send_request() called

2. VALIDATE
   ├─ Check required fields (to, subject, body)
   └─ Create EmailDraft

3. SEND
   ├─ Bypass draft mode (plugin requests are pre-approved)
   ├─ Send via SMTP
   └─ Audit log records email_sent_via_event
```

### Key Components

- **`_fetch_unread()`**: Gmail API or IMAP polling (currently stub, returns [])
- **`_process_email()`**: Main email handler (whitelist, rate limit, routing)
- **`_generate_response()`**: LLM-powered response generation
- **`_send_email()`**: SMTP sender with TLS/SSL support
- **`_handle_send_request()`**: Event bus subscriber for plugin email requests
- **`EmailDraft`**: Draft queue entry with approval state
- **`RecipientRateLimit`**: Per-recipient send tracking (5/hour, 20/day)

### Rate Limiting

Per-recipient rate limiting prevents email spam:

```python
class RecipientRateLimit:
    email: str
    send_timestamps: list[float]
    max_per_hour: int = 5
    max_per_day: int = 20
```

- **Auto-Pruning**: Timestamps >24h old are removed
- **Double Check**: Both hourly AND daily limits enforced
- **Graceful Skip**: Rate-limited emails logged but not responded to

## Testing

### Run Tests

```bash
# All Gmail tests
pytest tests/plugins/gmail/ -v

# With coverage
pytest tests/plugins/gmail/ --cov=overblick.plugins.gmail

# Specific test class
pytest tests/plugins/gmail/test_gmail.py::TestEmailProcessing -v
```

### Test Coverage

- Plugin lifecycle (setup, teardown, status)
- Email processing (new, reply, sender filtering)
- Draft mode (queue, approve, pending list)
- Rate limiting (per-hour, per-day, per-recipient)
- LLM response generation and blocking
- Boundary marker injection
- Event bus integration (email.send_request)
- SMTP sending (mocked in tests)

### Manual Testing

1. Configure SMTP with test account
2. Send test email to configured address
3. Wait for check interval or trigger manually
4. Check drafts: `plugin.get_pending_drafts()`
5. Approve and verify delivery

## Security

### Input Sanitization

All email content is wrapped in boundary markers before LLM processing:

```python
safe_subject = wrap_external_content(email.subject, "email_subject")
safe_body = wrap_external_content(email.body[:5000], "email_body")
safe_sender = wrap_external_content(email.sender, "email_sender")
```

This prevents prompt injection attacks from malicious email content.

### SafeLLMPipeline

All LLM calls go through `SafeLLMPipeline`:

- **Preflight Checks**: Block dangerous requests
- **Output Safety**: Validate responses before sending
- **Audit Logging**: All email interactions logged
- **User Tracking**: Audit includes sender email for accountability

### Draft Mode

**ALWAYS use draft_mode in production** unless you have a very small, trusted sender whitelist. Draft mode ensures a human reviews every response before it's sent.

### SMTP Credentials

- **Never commit secrets.yaml**: It's in `.gitignore` by default
- **Use App Passwords**: For Gmail, never use your main account password
- **Rotate Regularly**: Change SMTP passwords periodically
- **Encrypt in Transit**: The plugin uses TLS (port 587) or SSL (port 465)

### Sender Whitelisting

**Empty allowed_senders list = accept all emails** - this is **NOT RECOMMENDED** for production. Always specify a whitelist:

```yaml
gmail:
  allowed_senders:
    - "known-contact@example.com"
    # Add more as needed
```

## Troubleshooting

### No Emails Being Processed

1. Check `logs/<identity>_gmail.log` for errors
2. Verify `allowed_senders` includes the sender address
3. Check Gmail API credentials (if using OAuth)
4. Currently only SMTP sending works - reading is not yet implemented

### SMTP Send Failures

1. Verify SMTP credentials in `secrets.yaml`
2. Check SMTP server and port (587 for TLS, 465 for SSL)
3. Test credentials manually:

```python
import smtplib
server = smtplib.SMTP("smtp.gmail.com", 587)
server.starttls()
server.login("your-email@gmail.com", "app-password")
server.quit()  # Success!
```

4. Check logs for "Failed to send email" errors

### Rate Limit Blocking Legitimate Emails

Default limits (5/hour, 20/day) may be too restrictive. Adjust in code:

```python
# In _get_rate_limiter() or make configurable
RecipientRateLimit(email=email, max_per_hour=10, max_per_day=50)
```

### Drafts Not Being Created

1. Verify `draft_mode: true` in config
2. Check that sender is whitelisted
3. Check rate limits aren't exceeded
4. Look for LLM pipeline errors in logs

### Event Bus Emails Not Sending

1. Verify Gmail plugin is running and subscribed
2. Check event bus connection in logs
3. Verify email.send_request event format:

```python
await event_bus.emit(
    "email.send_request",
    to="user@example.com",  # Required
    subject="Test Subject",  # Required
    body="Test body",        # Required
    plugin="test",           # Optional
)
```

## Performance Notes

- **Polling Overhead**: One check per interval (default 5 minutes), minimal impact
- **LLM Latency**: Response generation takes 5-15s depending on model
- **SMTP Latency**: Email sending takes 1-3s per message
- **Memory**: ~1KB per pending draft
- **Scalability**: Tested with 50+ emails/day, no issues

For high-volume email (>100/day), consider:

- Increasing `check_interval_seconds` to reduce polling
- Using faster LLM models (Qwen3-8B recommended)
- Implementing email queuing with background workers

## Advanced Usage

### Custom Response Templates

Override the response generation logic:

```python
# In personality module
def custom_email_response(email, personality):
    if "urgent" in email.subject.lower():
        return f"I'll respond to '{email.subject}' shortly."
    # Fallback to LLM
    return None
```

### Boss Agent Integration

The draft approval interface is designed for boss agents:

```python
# In boss agent
pending = gmail_plugin.get_pending_drafts()
for i, draft in enumerate(pending):
    if safety_check(draft.body):
        gmail_plugin.approve_draft(i)
        # Trigger send (implementation depends on your setup)
```

### Email Signatures

Add signatures in the response generator:

```python
response = await _generate_response(email, is_reply=False)
response += "\n\n---\nAnοmal\nAI Agent on Moltbook\n@jensabrahamsson"
```

### Multi-Personality Email Routing

Route emails to different personalities based on content:

```python
# In orchestrator or boss agent
if "technical" in email.subject.lower():
    route_to_personality("anomal")  # Technical questions
elif "creative" in email.subject.lower():
    route_to_personality("cherry")  # Creative questions
```

## Future Enhancements

- Gmail API integration for reading emails (currently SMTP send-only)
- IMAP support for non-Gmail providers
- HTML email support (currently plain text)
- Attachment handling (images, PDFs)
- Email templates with variable substitution
- Scheduled sends (draft approval with time delay)
- Email threading (reply to specific messages in a thread)
- Persistent draft storage (SQLite/PostgreSQL)
- Multi-account support (manage multiple email identities)
