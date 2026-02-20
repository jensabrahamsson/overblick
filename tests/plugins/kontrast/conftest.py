"""Test fixtures for KontrastPlugin."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.event_bus import EventBus
from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.core.plugin_base import PluginContext
from overblick.identities import Personality, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.plugins.kontrast.plugin import KontrastPlugin


@pytest.fixture
def kontrast_identity():
    """Test identity for kontrast plugin tests."""
    return Personality(
        name="test",
        display_name="Test",
        description="Test identity for kontrast plugin",
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "kontrast": {
                "feeds": [
                    "https://example.com/feed1.xml",
                    "https://example.com/feed2.xml",
                ],
                "interval_hours": 24,
                "min_articles": 2,
                "identities": ["anomal", "cherry"],
            },
        },
    )


@pytest.fixture
def mock_pipeline():
    """Mock SafeLLMPipeline with default success response."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(
            content='{"topic": "AI Safety Debate", "summary": "Major debate on AI regulation."}'
        )
    )
    return pipeline


@pytest.fixture
def mock_pipeline_blocked():
    """Mock SafeLLMPipeline that returns a blocked result."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(
            blocked=True,
            block_reason="Safety check failed",
            block_stage=PipelineStage.OUTPUT_SAFETY,
        )
    )
    return pipeline


@pytest.fixture
def mock_event_bus():
    """Mock event bus."""
    bus = AsyncMock(spec=EventBus)
    bus.emit = AsyncMock(return_value=1)
    return bus


@pytest.fixture
def kontrast_context(
    kontrast_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
    mock_event_bus,
):
    """Full plugin context for kontrast tests."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=kontrast_identity,
        engagement_db=MagicMock(),
    )
    return ctx
