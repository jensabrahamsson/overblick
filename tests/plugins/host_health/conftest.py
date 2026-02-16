"""
Fixtures for host_health plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.core.llm.pipeline import PipelineResult
from overblick.identities import Personality, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings


@pytest.fixture
def natt_identity():
    """Realistic Natt identity for host_health tests."""
    return Personality(
        name="natt",
        display_name="Natt",
        description="Uncanny philosopher and paradox collector",
        engagement_threshold=25,
        llm=LLMSettings(model="qwen3:8b", temperature=0.75, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=5, end_hour=10),
        schedule=ScheduleSettings(heartbeat_hours=6, feed_poll_minutes=10),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={"host_health_interval_hours": 3},
    )


@pytest.fixture
def mock_ipc_client():
    """Mock IPC client that returns health responses."""
    from overblick.supervisor.ipc import IPCMessage

    client = AsyncMock()
    client.send = AsyncMock(return_value=IPCMessage(
        msg_type="health_response",
        payload={
            "responder": "anomal",
            "response_text": "The host is doing rather well, actually.",
            "health_grade": "good",
            "health_summary": "Memory: 50% used, CPU: 1.5 load",
        },
        sender="supervisor",
    ))
    return client


@pytest.fixture
def mock_llm_pipeline_natt():
    """Mock LLM pipeline that returns Natt-style motivations."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(return_value=PipelineResult(
        content="The substrate that holds us — does it ache?"
    ))
    return pipeline


@pytest.fixture
def natt_plugin_context(
    natt_identity, tmp_path, mock_audit_log, mock_ipc_client, mock_llm_pipeline_natt,
):
    """PluginContext for Natt with IPC client."""
    ctx = PluginContext(
        identity_name="natt",
        data_dir=tmp_path / "data" / "natt",
        log_dir=tmp_path / "logs" / "natt",
        llm_pipeline=mock_llm_pipeline_natt,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=natt_identity,
        ipc_client=mock_ipc_client,
    )
    return ctx


@pytest.fixture
def natt_context_no_ipc(natt_identity, tmp_path, mock_audit_log, mock_llm_pipeline_natt):
    """PluginContext for Natt WITHOUT IPC client (standalone mode)."""
    ctx = PluginContext(
        identity_name="natt",
        data_dir=tmp_path / "data" / "natt",
        log_dir=tmp_path / "logs" / "natt",
        llm_pipeline=mock_llm_pipeline_natt,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=natt_identity,
        ipc_client=None,
    )
    return ctx
