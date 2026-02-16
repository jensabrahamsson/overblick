"""
Fixtures for email_agent plugin tests.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.core.llm.pipeline import PipelineResult
from overblick.identities import (
    Personality,
    LLMSettings,
    QuietHoursSettings,
    ScheduleSettings,
    SecuritySettings,
)


@pytest.fixture
def stal_identity():
    """Realistic Stål identity for email_agent tests."""
    return Personality(
        name="stal",
        display_name="Stål",
        description="Executive secretary and email agent",
        engagement_threshold=25,
        llm=LLMSettings(model="qwen3:8b", temperature=0.4, max_tokens=1500),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=22, end_hour=6),
        schedule=ScheduleSettings(heartbeat_hours=1, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            "email_agent": {
                "filter_mode": "opt_in",
                "allowed_senders": ["jens@example.com", "test@example.com"],
                "blocked_senders": [],
                "reputation": {
                    "sender_ignore_rate": 0.9,
                    "sender_min_interactions": 5,
                    "domain_ignore_rate": 0.9,
                    "domain_min_interactions": 10,
                },
                "consultation": {
                    "confidence_low": 0.5,
                    "confidence_high": 0.8,
                },
                "relevance_consultants": [
                    {"identity": "anomal", "keywords": ["crypto", "bitcoin", "blockchain", "ai", "tech"]},
                    {"identity": "blixt", "keywords": ["privacy", "security", "surveillance"]},
                ],
            }
        },
    )


@pytest.fixture
def mock_ipc_client_email():
    """Mock IPC client that returns email consultation responses."""
    from overblick.supervisor.ipc import IPCMessage

    client = AsyncMock()
    client.send = AsyncMock(return_value=IPCMessage(
        msg_type="email_consultation_response",
        payload={
            "advised_action": "reply",
            "reasoning": "This appears to be a legitimate meeting request",
        },
        sender="supervisor",
    ))
    return client


@pytest.fixture
def mock_llm_pipeline_classify():
    """Mock LLM pipeline that returns classification JSON."""
    pipeline = AsyncMock()

    # Default: classify as REPLY with high confidence
    pipeline.chat = AsyncMock(return_value=PipelineResult(
        content='{"intent": "reply", "confidence": 0.95, "reasoning": "Meeting request from colleague", "priority": "normal"}'
    ))
    return pipeline


@pytest.fixture
def mock_event_bus():
    """Mock event bus for email sending."""
    bus = MagicMock()
    bus.emit = MagicMock()
    bus.subscribe = MagicMock()
    return bus


@pytest.fixture
def mock_telegram_notifier():
    """Mock Telegram notifier capability."""
    notifier = AsyncMock()
    notifier.send_notification = AsyncMock(return_value=True)
    notifier.send_notification_tracked = AsyncMock(return_value=42)
    notifier.fetch_updates = AsyncMock(return_value=[])
    notifier.configured = True
    notifier._chat_id = "12345"
    return notifier


@pytest.fixture
def mock_boss_request_capability():
    """Mock BossRequest capability for research."""
    cap = AsyncMock()
    cap.configured = True
    cap.request_research = AsyncMock(return_value="Research summary: The topic is well documented.")
    return cap


@pytest.fixture
def mock_gmail_capability():
    """Mock Gmail capability for fetching and sending email."""
    cap = AsyncMock()
    cap.fetch_unread = AsyncMock(return_value=[])
    cap.send_reply = AsyncMock(return_value=True)
    cap.mark_as_read = AsyncMock(return_value=True)
    return cap


@pytest.fixture
def mock_personality_consultant():
    """Mock PersonalityConsultantCapability for cross-identity consultation."""
    cap = AsyncMock()
    cap.consult = AsyncMock(return_value="YES — this appears relevant to the principal.")
    return cap


@pytest.fixture
def stal_plugin_context(
    stal_identity, tmp_path, mock_audit_log, mock_ipc_client_email,
    mock_llm_pipeline_classify, mock_event_bus, mock_telegram_notifier,
    mock_gmail_capability, mock_boss_request_capability,
    mock_personality_consultant,
):
    """PluginContext for Stål with all required services."""
    def _mock_secrets(key: str):
        secrets = {"principal_name": "Test Principal"}
        return secrets.get(key)

    ctx = PluginContext(
        identity_name="stal",
        data_dir=tmp_path / "data" / "stal",
        log_dir=tmp_path / "logs" / "stal",
        llm_pipeline=mock_llm_pipeline_classify,
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=stal_identity,
        ipc_client=mock_ipc_client_email,
        capabilities={
            "telegram_notifier": mock_telegram_notifier,
            "gmail": mock_gmail_capability,
            "boss_request": mock_boss_request_capability,
            "personality_consultant": mock_personality_consultant,
        },
    )
    ctx._secrets_getter = _mock_secrets
    return ctx


@pytest.fixture
def stal_context_no_ipc(
    stal_identity, tmp_path, mock_audit_log, mock_llm_pipeline_classify,
    mock_event_bus,
):
    """PluginContext for Stål WITHOUT IPC (standalone mode)."""
    def _mock_secrets(key: str):
        secrets = {"principal_name": "Test Principal"}
        return secrets.get(key)

    ctx = PluginContext(
        identity_name="stal",
        data_dir=tmp_path / "data" / "stal",
        log_dir=tmp_path / "logs" / "stal",
        llm_pipeline=mock_llm_pipeline_classify,
        event_bus=mock_event_bus,
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=stal_identity,
        ipc_client=None,
    )
    ctx._secrets_getter = _mock_secrets
    return ctx


def make_email(sender: str, subject: str, body: str, snippet: str = "") -> dict:
    """Helper to create a test email dict."""
    return {
        "sender": sender,
        "subject": subject,
        "body": body,
        "snippet": snippet or body[:200],
        "message_id": "test-msg-001",
        "thread_id": "test-thread-001",
    }
