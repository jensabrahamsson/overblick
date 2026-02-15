"""Test fixtures for AiDigestPlugin."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.event_bus import EventBus
from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.core.plugin_base import PluginContext
from overblick.personalities import Personality, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.plugins.ai_digest.plugin import AiDigestPlugin


# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ai_digest_identity():
    """Test identity for ai_digest plugin tests."""
    return Personality(
        name="test",
        display_name="Test",
        description="Test identity for ai_digest plugin",
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "ai_digest": {
                "feeds": [
                    "https://example.com/feed1.xml",
                    "https://example.com/feed2.xml",
                ],
                "recipient": "test@example.com",
                "hour": 7,
                "timezone": "Europe/Stockholm",
                "top_n": 5,
            },
        },
    )


# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pipeline():
    """Mock SafeLLMPipeline with default success response."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="[1, 3, 5, 2, 4]")
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
    """Mock event bus for testing email dispatch."""
    bus = AsyncMock(spec=EventBus)
    bus.emit = AsyncMock(return_value=1)
    return bus


@pytest.fixture
def mock_email_capability():
    """Mock email capability for testing digest sending."""
    cap = AsyncMock()
    cap.name = "email"
    cap.send = AsyncMock(return_value=True)
    return cap


# ---------------------------------------------------------------------------
# PluginContext fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ai_digest_context(
    ai_digest_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
    mock_event_bus, mock_email_capability,
):
    """Full plugin context for ai_digest tests."""
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
        identity=ai_digest_identity,
        engagement_db=MagicMock(),
        capabilities={"email": mock_email_capability},
    )
    return ctx


@pytest.fixture
def ai_digest_context_quiet(
    ai_digest_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
    mock_event_bus,
):
    """Plugin context with quiet hours active."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=True)),
        identity=ai_digest_identity,
        engagement_db=MagicMock(),
    )
    return ctx


@pytest.fixture
def ai_digest_context_no_recipient(
    tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
):
    """Plugin context with missing recipient (for failure tests)."""
    identity = Personality(
        name="test",
        display_name="Test",
        llm=LLMSettings(),
        raw_config={"ai_digest": {"feeds": ["https://example.com/feed.xml"]}},
    )
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=identity,
    )
    return ctx
