"""Fixtures for log agent plugin tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.identities import (
    LLMSettings,
    Personality,
    QuietHoursSettings,
    ScheduleSettings,
    SecuritySettings,
)


@pytest.fixture
def vakt_identity():
    """Realistic Vakt identity for log_agent tests."""
    return Personality(
        name="vakt",
        display_name="Vakt",
        description="Log monitoring and alerting agent",
        engagement_threshold=25,
        llm=LLMSettings(model="qwen3:8b", temperature=0.2, max_tokens=1500),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=1, end_hour=4),
        schedule=ScheduleSettings(heartbeat_hours=1, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "log_agent": {
                "scan_identities": ["anomal", "cherry", "stal"],
                "tick_interval_minutes": 5,
                "dry_run": True,
                "alerting": {
                    "cooldown_seconds": 3600,
                },
            }
        },
    )


@pytest.fixture
def vakt_plugin_context(vakt_identity, tmp_path, mock_audit_log):
    """PluginContext for Vakt with mocked services."""
    mock_notifier = AsyncMock()
    mock_notifier.send_notification = AsyncMock(return_value=True)
    mock_notifier.configured = True

    ctx = PluginContext(
        identity_name="vakt",
        data_dir=tmp_path / "data" / "vakt",
        log_dir=tmp_path / "logs" / "vakt",
        llm_pipeline=AsyncMock(),
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=vakt_identity,
        capabilities={
            "telegram_notifier": mock_notifier,
        },
    )
    return ctx


@pytest.fixture
def sample_log_dir(tmp_path):
    """Create sample log files for testing."""
    log_base = tmp_path / "logs"

    # anomal logs
    anomal_dir = log_base / "anomal"
    anomal_dir.mkdir(parents=True)
    (anomal_dir / "anomal.log").write_text(
        "2026-02-26 03:00:01,000 - core.llm - INFO - LLM request started\n"
        "2026-02-26 03:00:02,000 - core.llm - ERROR - LLM call failed: timeout\n"
        "  Traceback (most recent call last):\n"
        "    File \"pipeline.py\", line 42\n"
        "    TimeoutError: Request timed out\n"
        "2026-02-26 03:00:03,000 - plugins.moltbook - INFO - Tick complete\n"
        "2026-02-26 03:00:04,000 - core.security - CRITICAL - Preflight check blocked dangerous input\n"
    )

    # cherry logs (clean)
    cherry_dir = log_base / "cherry"
    cherry_dir.mkdir(parents=True)
    (cherry_dir / "cherry.log").write_text(
        "2026-02-26 03:00:01,000 - plugins.moltbook - INFO - Tick complete\n"
        "2026-02-26 03:00:02,000 - plugins.moltbook - INFO - Posted 3 replies\n"
    )

    # stal logs
    stal_dir = log_base / "stal"
    stal_dir.mkdir(parents=True)
    (stal_dir / "stal.log").write_text(
        "2026-02-26 03:00:01,000 - plugins.email - ERROR - Gmail API rate limited\n"
        "2026-02-26 03:00:02,000 - plugins.email - ERROR - Gmail API rate limited\n"
    )

    return log_base
