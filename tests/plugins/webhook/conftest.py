"""
Shared fixtures for Webhook plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from blick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from blick.core.llm.pipeline import PipelineResult
from blick.core.plugin_base import PluginContext
from blick.plugins.webhook.plugin import WebhookPlugin


@pytest.fixture
def webhook_identity():
    """Identity configured for Webhook plugin testing."""
    return Identity(
        name="prism",
        display_name="Prism",
        description="Analytical pattern agent",
        engagement_threshold=30,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["patterns", "data", "analysis"],
        raw_config={
            "agent_name": "Prism",
            "webhook": {
                "host": "0.0.0.0",
                "port": 4567,
                "path": "/hooks/prism",
            },
        },
    )


@pytest.fixture
def webhook_pipeline():
    """Mock SafeLLMPipeline for Webhook tests."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="Webhook event processed.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


@pytest.fixture
def webhook_context(webhook_identity, tmp_path, mock_llm_client, mock_audit_log, webhook_pipeline):
    """PluginContext wired for Webhook plugin."""
    ctx = PluginContext(
        identity_name=webhook_identity.name,
        data_dir=tmp_path / "data" / "prism",
        log_dir=tmp_path / "logs" / "prism",
        llm_client=mock_llm_client,
        llm_pipeline=webhook_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=webhook_identity,
    )
    ctx._secrets_getter = lambda key: {
        "webhook_hmac_secret": "test-hmac-secret-xyz",
    }.get(key)
    return ctx


@pytest.fixture
async def webhook_plugin(webhook_context):
    """Set up WebhookPlugin with mocked context."""
    plugin = WebhookPlugin(webhook_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()
