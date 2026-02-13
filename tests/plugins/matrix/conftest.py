"""
Shared fixtures for Matrix plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.llm.pipeline import PipelineResult
from overblick.core.plugin_base import PluginContext
from overblick.plugins.matrix.plugin import MatrixPlugin


@pytest.fixture
def matrix_identity():
    """Identity configured for Matrix plugin testing."""
    return Identity(
        name="volt",
        display_name="Volt",
        description="Privacy-focused punk on Matrix",
        engagement_threshold=30,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=1000),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["privacy", "decentralization", "encryption"],
        raw_config={
            "agent_name": "Volt",
            "matrix": {
                "homeserver": "https://matrix.example.org",
                "user_id": "@volt:example.org",
                "room_ids": [
                    "!abc123:example.org",
                    "!def456:example.org",
                ],
            },
        },
    )


@pytest.fixture
def matrix_pipeline():
    """Mock SafeLLMPipeline for Matrix tests."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="Privacy is a fundamental right.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


@pytest.fixture
def matrix_context(matrix_identity, tmp_path, mock_llm_client, mock_audit_log, matrix_pipeline):
    """PluginContext wired for Matrix plugin."""
    ctx = PluginContext(
        identity_name=matrix_identity.name,
        data_dir=tmp_path / "data" / "volt",
        log_dir=tmp_path / "logs" / "volt",
        llm_client=mock_llm_client,
        llm_pipeline=matrix_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=matrix_identity,
    )
    ctx._secrets_getter = lambda key: {
        "matrix_access_token": "syt_test_token_abc123",
    }.get(key)
    return ctx


@pytest.fixture
async def matrix_plugin(matrix_context):
    """Set up MatrixPlugin with mocked context."""
    plugin = MatrixPlugin(matrix_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()
