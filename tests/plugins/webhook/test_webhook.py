"""
Webhook plugin tests.

Tests cover:
- Plugin lifecycle (setup, tick, teardown)
- Configuration loading (host, port, path, HMAC)
- Status reporting
- Edge cases (missing config, no HMAC secret)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from blick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from blick.core.plugin_base import PluginContext
from blick.plugins.webhook.plugin import WebhookPlugin


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestWebhookLifecycle:
    """Test plugin lifecycle."""

    @pytest.mark.asyncio
    async def test_setup_loads_config(self, webhook_plugin):
        assert webhook_plugin._host == "0.0.0.0"
        assert webhook_plugin._port == 4567
        assert webhook_plugin._path == "/hooks/prism"

    @pytest.mark.asyncio
    async def test_setup_loads_hmac_secret(self, webhook_plugin):
        assert webhook_plugin._hmac_secret == "test-hmac-secret-xyz"

    @pytest.mark.asyncio
    async def test_setup_logs_audit_event(self, webhook_plugin):
        webhook_plugin.ctx.audit_log.log.assert_called()
        call_kwargs = webhook_plugin.ctx.audit_log.log.call_args[1]
        assert call_kwargs["action"] == "plugin_setup"
        assert "endpoint" in call_kwargs["details"]

    @pytest.mark.asyncio
    async def test_tick_is_noop_shell(self, webhook_plugin):
        await webhook_plugin.tick()

    @pytest.mark.asyncio
    async def test_teardown_completes(self, webhook_context):
        plugin = WebhookPlugin(webhook_context)
        await plugin.setup()
        await plugin.teardown()


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestWebhookConfig:
    """Test configuration handling."""

    @pytest.mark.asyncio
    async def test_default_config_values(self, webhook_context):
        """Plugin uses safe defaults when config section is missing."""
        webhook_context.identity = Identity(
            name="prism",
            display_name="Prism",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={},
        )
        plugin = WebhookPlugin(webhook_context)
        await plugin.setup()
        assert plugin._host == "127.0.0.1"
        assert plugin._port == 4567
        assert plugin._path == "/webhook"

    @pytest.mark.asyncio
    async def test_no_hmac_secret_is_allowed(self, webhook_context):
        """Plugin works without HMAC secret (shell mode)."""
        webhook_context._secrets_getter = lambda key: None
        plugin = WebhookPlugin(webhook_context)
        await plugin.setup()
        assert plugin._hmac_secret is None

    @pytest.mark.asyncio
    async def test_custom_port(self, webhook_context):
        webhook_context.identity = Identity(
            name="prism",
            display_name="Prism",
            description="Test",
            engagement_threshold=30,
            enabled_modules=(),
            llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
            quiet_hours=QuietHoursSettings(enabled=False),
            schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
            security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
            interest_keywords=[],
            raw_config={"webhook": {"port": 8080}},
        )
        plugin = WebhookPlugin(webhook_context)
        await plugin.setup()
        assert plugin._port == 8080


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------

class TestWebhookStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_status_structure(self, webhook_plugin):
        status = webhook_plugin.get_status()
        assert status["plugin"] == "webhook"
        assert status["identity"] == "prism"
        assert "endpoint" in status
        assert "http://" in status["endpoint"]
        assert status["webhooks_received"] == 0
        assert status["webhooks_processed"] == 0
        assert status["errors"] == 0

    @pytest.mark.asyncio
    async def test_status_endpoint_format(self, webhook_plugin):
        status = webhook_plugin.get_status()
        assert status["endpoint"] == "http://0.0.0.0:4567/hooks/prism"

    @pytest.mark.asyncio
    async def test_status_tracks_counters(self, webhook_plugin):
        webhook_plugin._webhooks_received = 100
        webhook_plugin._webhooks_processed = 95
        webhook_plugin._errors = 5
        status = webhook_plugin.get_status()
        assert status["webhooks_received"] == 100
        assert status["webhooks_processed"] == 95
        assert status["errors"] == 5

    @pytest.mark.asyncio
    async def test_plugin_name(self, webhook_plugin):
        assert webhook_plugin.name == "webhook"
