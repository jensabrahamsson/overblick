# Webhook Plugin

HTTP webhook receiver that accepts external service events and routes them through the personality-driven agent pipeline. **SHELL IMPLEMENTATION** - core structure in place, awaiting aiohttp integration and community contributions.

## Overview

The Webhook plugin will expose an HTTP endpoint that accepts webhook payloads from external services (GitHub, Stripe, custom integrations, IoT devices, etc.) and processes them through the agent's personality-driven pipeline. Perfect for agents that need to react to external events (CI/CD notifications, payment alerts, sensor data, etc.).

## Features (Planned)

- **HTTP Server**: Configurable endpoint (host, port, path)
- **HMAC Signature Verification**: Ensure webhook authenticity
- **Payload Parsing**: Support for common webhook formats (GitHub, Slack, generic JSON)
- **Personality-Driven Processing**: Route events through LLM for intelligent responses
- **Event Bus Integration**: Emit events for other plugins to consume
- **Request Logging**: Full audit trail of webhook requests
- **Rate Limiting**: Per-source-IP limits to prevent abuse
- **Payload Size Limits**: Prevent memory exhaustion attacks
- **Content Type Support**: JSON, form-encoded, multipart

## Current Status

This plugin is a **SHELL**. The structure is defined, but the implementation is incomplete:

- ✅ Plugin base class implemented
- ✅ Configuration loading (host, port, path)
- ✅ HMAC secret management
- ✅ Basic stats tracking
- ❌ HTTP server (requires aiohttp)
- ❌ Webhook routing and handlers
- ❌ HMAC signature verification
- ❌ Payload parsing for specific services
- ❌ Event bus emission
- ❌ Rate limiting

## Use Cases

### GitHub CI/CD Monitor (Anomal)

React to GitHub webhook events:

```yaml
webhook:
  host: "0.0.0.0"  # Listen on all interfaces
  port: 4567
  path: "/github"

  handlers:
    - event: "push"
      action: "notify_telegram"  # Post to Telegram when code is pushed
    - event: "pull_request"
      action: "code_review"  # Trigger AI code review
```

When a push happens, GitHub sends webhook → Agent posts to Telegram.

### Payment Processor (Cherry)

Monitor Stripe webhooks for payment events:

```yaml
webhook:
  port: 4567
  path: "/stripe"

  handlers:
    - event: "payment_intent.succeeded"
      action: "send_email"  # Thank customer via email
    - event: "payment_intent.failed"
      action: "alert"  # Notify admin
```

### IoT Sensor Monitor (Björk)

React to sensor data from IoT devices:

```yaml
webhook:
  port: 4567
  path: "/sensors"

  handlers:
    - event: "temperature_alert"
      action: "log_and_notify"  # Log data and notify if critical
```

## Setup

### Installation (Not Yet Functional)

When implemented, this plugin will require:

```bash
pip install aiohttp>=3.8.0
```

**Note**: Dependencies are not yet in requirements.txt as the plugin is not functional.

### Configuration

Add to `personality.yaml`:

```yaml
# Webhook Configuration (not yet functional)
webhook:
  # Host to bind to (default: 127.0.0.1 for localhost only)
  host: "127.0.0.1"  # Use "0.0.0.0" to expose publicly

  # Port to listen on (default: 4567)
  port: 4567

  # Webhook endpoint path (default: /webhook)
  path: "/webhook"

  # Max payload size in bytes (default: 1MB)
  max_payload_size: 1048576

  # Webhook handlers (event → action mapping)
  handlers:
    - event: "github.push"
      action: "notify_telegram"
      filter:
        branch: "main"  # Only trigger for main branch

    - event: "stripe.payment_succeeded"
      action: "send_email"

    - event: "custom.alert"
      action: "post_moltbook"
```

### Secrets

Add to `config/<identity>/secrets.yaml`:

```yaml
# HMAC secret for webhook signature verification
webhook_hmac_secret: "your-secret-key-here"

# Service-specific secrets
github_webhook_secret: "github-webhook-secret"
stripe_webhook_secret: "stripe-webhook-secret"
```

**How to get webhook secrets**:

- **GitHub**: Repo Settings → Webhooks → Add webhook → Secret
- **Stripe**: Developers → Webhooks → Add endpoint → Signing secret

### Activation

Include `webhook` in enabled plugins (when implemented):

```yaml
enabled_plugins:
  - webhook
  - telegram  # For notify_telegram action
```

## Architecture (Planned)

### Request Flow

```
1. RECEIVE
   ├─ HTTP POST to /webhook
   ├─ Extract headers (X-Hub-Signature, X-Stripe-Signature, etc.)
   ├─ Read request body (JSON, form-encoded, etc.)
   └─ Validate payload size (<= max_payload_size)

2. VERIFY
   ├─ Compute HMAC signature from body + secret
   ├─ Compare with signature header
   ├─ Reject if signatures don't match
   └─ Log verification success/failure

3. PARSE
   ├─ Detect service (GitHub, Stripe, custom)
   ├─ Parse JSON payload
   ├─ Extract event type (push, payment, etc.)
   └─ Validate required fields

4. ROUTE
   ├─ Match event against handlers
   ├─ Apply filters (branch, amount, etc.)
   └─ Select action (notify_telegram, send_email, post_moltbook)

5. PROCESS
   ├─ Wrap payload in boundary markers
   ├─ If action requires LLM:
   │  ├─ Build prompt with event context
   │  ├─ Call ctx.llm_pipeline.chat()
   │  └─ Extract action from response
   └─ Execute action (emit event, call API, etc.)

6. RESPOND
   ├─ Send HTTP 200 OK
   ├─ Log webhook processing to audit
   └─ Increment stats counter
```

### Key Components (To Be Implemented)

- **`_http_server`**: aiohttp web server instance
- **`_webhook_handler()`**: Main HTTP request handler
- **`_verify_signature()`**: HMAC signature verification
- **`_parse_github()`**: GitHub webhook parser
- **`_parse_stripe()`**: Stripe webhook parser
- **`_execute_action()`**: Action executor (Telegram, email, Moltbook)
- **`WebhookEvent`**: Dataclass for parsed webhook events

## Events

### Emits (Planned)

- **`webhook.received`** - Generic webhook received
  - `service`: Service name (github, stripe, custom)
  - `event_type`: Event type (push, payment, etc.)
  - `payload`: Parsed payload (dict)

- **`webhook.github.push`** - GitHub push event
  - `repo`: Repository name
  - `branch`: Branch name
  - `commits`: List of commits

- **`webhook.stripe.payment`** - Stripe payment event
  - `amount`: Payment amount
  - `currency`: Currency code
  - `status`: Payment status

### Subscribes (Planned)

None initially. The webhook plugin is an entry point (receives external events).

## Usage (When Implemented)

### Running the Webhook Server

```bash
# Start agent with Webhook plugin
python -m overblick run anomal

# The webhook server will start:
# Webhook server listening at http://127.0.0.1:4567/webhook

# Configure external service to send webhooks to this URL
```

### Exposing to Internet

For production, use a reverse proxy (nginx, Cloudflare Tunnel):

```nginx
# nginx configuration
server {
    listen 80;
    server_name webhooks.example.com;

    location /github {
        proxy_pass http://127.0.0.1:4567/webhook;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Or use Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:4567
```

### Testing Webhook

Use curl to send a test webhook:

```bash
# Send test webhook
curl -X POST http://127.0.0.1:4567/webhook \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=<computed-hmac>" \
  -d '{
    "event": "test",
    "message": "Hello webhook"
  }'
```

## Testing

### Run Tests

```bash
# Tests for the shell implementation
pytest tests/plugins/webhook/ -v
```

**Note**: Tests currently verify the shell structure. Full integration tests will be added when aiohttp is integrated.

## Security (Critical for Webhooks)

### HMAC Signature Verification

**REQUIRED for production**. Without signature verification, anyone can send fake webhooks.

```python
import hmac
import hashlib

def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

GitHub signature header: `X-Hub-Signature-256`
Stripe signature header: `X-Stripe-Signature`

### Rate Limiting

Prevent abuse by limiting requests per IP:

```python
# Max 100 requests per hour per IP
rate_limiter = {
    "192.168.1.1": {"count": 5, "reset_at": time.time() + 3600}
}
```

### Payload Size Limits

Prevent memory exhaustion:

```python
MAX_PAYLOAD_SIZE = 1_048_576  # 1MB

if len(body) > MAX_PAYLOAD_SIZE:
    return web.Response(status=413)  # Payload Too Large
```

### Input Sanitization

All webhook payload content wrapped in boundary markers:

```python
safe_payload = wrap_external_content(json.dumps(payload), "webhook_payload")
```

### IP Whitelisting (Optional)

Restrict to specific source IPs:

```yaml
webhook:
  allowed_ips:
    - "192.30.252.0/22"  # GitHub webhook IPs
    - "140.82.112.0/20"  # GitHub webhook IPs
```

## Contributing

This plugin is marked as **COMMUNITY CONTRIBUTIONS WELCOME**. If you'd like to implement the webhook integration:

### Implementation Checklist

- [ ] Add aiohttp dependency to pyproject.toml
- [ ] Implement HTTP server in `setup()`
- [ ] Add HMAC signature verification
- [ ] Implement GitHub webhook parser
- [ ] Implement Stripe webhook parser
- [ ] Add generic JSON webhook support
- [ ] Implement rate limiting per source IP
- [ ] Add payload size validation
- [ ] Implement action executors (notify_telegram, send_email, etc.)
- [ ] Write integration tests with mock webhooks
- [ ] Update this README with actual usage examples

### Code Structure

```python
# overblick/plugins/webhook/plugin.py

from aiohttp import web
import hmac
import hashlib

class WebhookPlugin(PluginBase):
    async def setup(self):
        # Create aiohttp web app
        self._app = web.Application()
        self._app.router.add_post(self._path, self._webhook_handler)

        # Start HTTP server in background
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()

        logger.info(f"Webhook server listening at http://{self._host}:{self._port}{self._path}")

    async def _webhook_handler(self, request: web.Request) -> web.Response:
        """Main webhook request handler."""
        # 1. Read body
        body = await request.read()
        if len(body) > self._max_payload_size:
            return web.Response(status=413)

        # 2. Verify signature
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not self._verify_signature(body, signature):
            logger.warning("Invalid webhook signature")
            return web.Response(status=401)

        # 3. Parse payload
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.Response(status=400)

        # 4. Route to handler
        event_type = payload.get("event", "unknown")
        await self._process_webhook(event_type, payload)

        # 5. Respond
        self._webhooks_received += 1
        return web.Response(status=200, text="OK")

    def _verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify HMAC signature."""
        if not self._hmac_secret:
            return True  # Skip verification if no secret configured

        expected = "sha256=" + hmac.new(
            self._hmac_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _process_webhook(self, event_type: str, payload: dict):
        """Process webhook event."""
        # Route to handlers based on event type
        ...
```

### Testing Approach

Use aiohttp test client:

```python
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

async def test_webhook_handler(aio_client):
    plugin = WebhookPlugin(...)
    await plugin.setup()

    async with TestClient(TestServer(plugin._app)) as client:
        resp = await client.post("/webhook", json={"event": "test"})
        assert resp.status == 200
```

### Pull Request Guidelines

1. Ensure all tests pass: `pytest tests/plugins/webhook/ -v`
2. Add integration tests for GitHub, Stripe, custom webhooks
3. Update this README with actual usage examples
4. Document signature verification for each service
5. Follow existing code style (type hints, docstrings)

## Configuration Examples

### GitHub Webhooks

```yaml
webhook:
  host: "0.0.0.0"  # Expose to internet
  port: 4567
  path: "/github"

  handlers:
    - event: "push"
      action: "notify_telegram"
      filter:
        branch: "main"

    - event: "pull_request"
      action: "post_moltbook"
      filter:
        action: "opened"

    - event: "issues"
      action: "email"
      filter:
        action: "opened"
        label: "bug"
```

GitHub webhook URL: `https://your-domain.com/github`

### Stripe Webhooks

```yaml
webhook:
  port: 4567
  path: "/stripe"

  handlers:
    - event: "payment_intent.succeeded"
      action: "send_email"

    - event: "payment_intent.failed"
      action: "notify_telegram"

    - event: "customer.subscription.created"
      action: "update_database"
```

Stripe webhook URL: `https://your-domain.com/stripe`

### Custom IoT Webhooks

```yaml
webhook:
  port: 4567
  path: "/iot"

  handlers:
    - event: "sensor.temperature"
      action: "log"
      filter:
        value_gt: 30  # Only log if temp > 30°C

    - event: "sensor.motion"
      action: "notify_telegram"
```

## Webhook Services Reference

### GitHub

- **Signature Header**: `X-Hub-Signature-256`
- **Events**: push, pull_request, issues, release, etc.
- **Documentation**: [GitHub Webhooks](https://docs.github.com/webhooks)

### Stripe

- **Signature Header**: `X-Stripe-Signature`
- **Events**: payment_intent, customer, subscription, etc.
- **Documentation**: [Stripe Webhooks](https://stripe.com/docs/webhooks)

### Slack

- **Signature Header**: `X-Slack-Signature`
- **Events**: message, app_mention, etc.
- **Documentation**: [Slack Events API](https://api.slack.com/events-api)

### Custom Webhooks

For custom services, use generic JSON format:

```json
{
  "event": "custom.event_name",
  "timestamp": "2026-02-14T12:00:00Z",
  "data": {
    "key": "value"
  }
}
```

## Future Enhancements (Post-Implementation)

- Webhook retry mechanism (if handler fails)
- Webhook queue (process in background)
- Webhook history (store last N webhooks)
- Webhook playground (test webhooks in UI)
- Webhook transformations (modify payload before processing)
- Multi-tenant webhooks (different handlers per identity)
- Webhook analytics (dashboard of received events)
- Integration with Zapier, IFTTT, n8n

## References

- [aiohttp Web Server](https://docs.aiohttp.org/en/stable/web.html)
- [GitHub Webhooks](https://docs.github.com/webhooks)
- [Stripe Webhooks](https://stripe.com/docs/webhooks)
- [HMAC Authentication](https://en.wikipedia.org/wiki/HMAC)

## Support

For questions or to contribute to this plugin:

1. Check the issues list for existing discussions
2. Submit a PR with your implementation
3. Reach out to @jensabrahamsson for coordination

**Status**: Shell implementation awaiting community contribution. The foundation is solid - we need someone with aiohttp experience to wire up the HTTP server and webhook routing!
