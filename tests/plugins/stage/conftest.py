"""Test fixtures for StagePlugin."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.event_bus import EventBus
from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.core.plugin_base import PluginContext
from overblick.identities import Personality, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.plugins.stage.plugin import StagePlugin


@pytest.fixture
def stage_identity():
    """Test identity for stage plugin tests."""
    return Personality(
        name="test",
        display_name="Test",
        description="Test identity for stage plugin",
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "stage": {
                "scenario_dirs": [],
            },
        },
    )


@pytest.fixture
def mock_pipeline_stage():
    """Mock SafeLLMPipeline for scenario execution."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(
            content="I'd rather talk about something that truly interests me — love, attachment, and how we connect with each other."
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
def stage_context(
    stage_identity, tmp_path, mock_llm_client, mock_audit_log,
    mock_pipeline_stage, mock_event_bus,
):
    """Full plugin context for stage tests."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline_stage,
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=stage_identity,
        engagement_db=MagicMock(),
    )
    return ctx
