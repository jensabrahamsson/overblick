"""Test fixtures for CompassPlugin."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.event_bus import EventBus
from overblick.core.plugin_base import PluginContext
from overblick.identities import Personality, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.plugins.compass.plugin import CompassPlugin


@pytest.fixture
def compass_identity():
    """Test identity for compass plugin tests."""
    return Personality(
        name="test",
        display_name="Test",
        description="Test identity for compass plugin",
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "compass": {
                "window_size": 10,
                "baseline_samples": 5,
                "drift_threshold": 2.0,
            },
        },
    )


@pytest.fixture
def mock_event_bus():
    """Mock event bus."""
    bus = MagicMock()
    bus.subscribe = MagicMock()
    bus.emit = AsyncMock(return_value=1)
    return bus


@pytest.fixture
def compass_context(
    compass_identity, tmp_path, mock_llm_client, mock_audit_log, mock_event_bus,
):
    """Full plugin context for compass tests."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=None,  # Compass doesn't use LLM
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=compass_identity,
        engagement_db=MagicMock(),
    )
    return ctx
