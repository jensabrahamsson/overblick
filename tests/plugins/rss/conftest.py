"""
Shared fixtures for RSS plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from blick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from blick.core.llm.pipeline import PipelineResult
from blick.core.plugin_base import PluginContext
from blick.plugins.rss.plugin import RSSPlugin


@pytest.fixture
def rss_identity():
    """Identity configured for RSS plugin testing."""
    return Identity(
        name="birch",
        display_name="Birch",
        description="Contemplative nature agent",
        engagement_threshold=30,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["ecology", "forests", "mindfulness"],
        raw_config={
            "agent_name": "Birch",
            "rss": {
                "feeds": [
                    {
                        "url": "https://example.com/nature.rss",
                        "name": "Nature News",
                        "poll_interval_minutes": 15,
                        "keywords": ["forest", "ecology"],
                    },
                    {
                        "url": "https://example.com/tech.rss",
                        "name": "Tech Feed",
                    },
                ],
            },
        },
    )


@pytest.fixture
def rss_pipeline():
    """Mock SafeLLMPipeline for RSS tests."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="A thoughtful summary of the article.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


@pytest.fixture
def rss_context(rss_identity, tmp_path, mock_llm_client, mock_audit_log, rss_pipeline):
    """PluginContext wired for RSS plugin."""
    ctx = PluginContext(
        identity_name=rss_identity.name,
        data_dir=tmp_path / "data" / "birch",
        log_dir=tmp_path / "logs" / "birch",
        llm_client=mock_llm_client,
        llm_pipeline=rss_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=rss_identity,
    )
    ctx._secrets_getter = lambda key: None
    return ctx


@pytest.fixture
async def rss_plugin(rss_context):
    """Set up RSSPlugin with mocked context."""
    plugin = RSSPlugin(rss_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()
