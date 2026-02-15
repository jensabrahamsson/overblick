"""
WebhookPlugin — HTTP webhook receiver for the Överblick framework.

Exposes an HTTP endpoint that accepts webhook payloads from external
services (GitHub, Stripe, custom integrations) and routes them through
the agent's personality-driven pipeline.

Features (planned):
- Configurable HTTP endpoint (host, port, path)
- HMAC signature verification for webhook authenticity
- Payload parsing for common webhook formats (GitHub, Slack, generic JSON)
- Personality-driven response generation
- Event routing to other plugins via event bus
- Request logging and audit trail

Dependencies (not yet added):
- aiohttp (HTTP server)

Security considerations:
- HMAC signature verification REQUIRED for production
- Rate limiting per source IP
- Payload size limits
- No arbitrary code execution from webhook payloads

This is a SHELL — community contributions welcome!
"""

import logging
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

logger = logging.getLogger(__name__)


class WebhookPlugin(PluginBase):
    """
    HTTP webhook receiver plugin (shell).

    Accepts incoming webhooks and processes them through
    the agent's personality pipeline.
    """

    name = "webhook"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._host: str = "127.0.0.1"
        self._port: int = 4567
        self._path: str = "/webhook"
        self._hmac_secret: Optional[str] = None
        self._webhooks_received = 0
        self._webhooks_processed = 0
        self._errors = 0

    async def setup(self) -> None:
        """
        Initialize the webhook server.

        Loads endpoint config and HMAC secret. Full HTTP server
        requires aiohttp as a dependency.
        """
        identity = self.ctx.identity
        raw_config = identity.raw_config
        webhook_config = raw_config.get("webhook", {})

        self._host = webhook_config.get("host", "127.0.0.1")
        self._port = webhook_config.get("port", 4567)
        self._path = webhook_config.get("path", "/webhook")
        self._hmac_secret = self.ctx.get_secret("webhook_hmac_secret")

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "endpoint": f"http://{self._host}:{self._port}{self._path}",
            },
        )

        logger.info(
            "WebhookPlugin setup complete for %s (%s:%d%s, shell mode)",
            identity.name, self._host, self._port, self._path,
        )

    async def tick(self) -> None:
        """
        Process queued webhook events.

        The HTTP server runs independently. tick() processes queued
        events needing LLM responses, handles retries, and collects
        metrics.
        """
        pass

    async def teardown(self) -> None:
        """Stop the HTTP server."""
        logger.info("WebhookPlugin teardown complete")

    def get_status(self) -> dict:
        """Get plugin status."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "endpoint": f"http://{self._host}:{self._port}{self._path}",
            "webhooks_received": self._webhooks_received,
            "webhooks_processed": self._webhooks_processed,
            "errors": self._errors,
        }
