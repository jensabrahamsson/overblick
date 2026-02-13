"""
Shared fixtures for Gmail plugin tests.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from blick.core.identity import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from blick.core.llm.pipeline import PipelineResult, PipelineStage
from blick.core.plugin_base import PluginContext
from blick.plugins.gmail.plugin import EmailDraft, EmailMessage, GmailPlugin


# ---------------------------------------------------------------------------
# Identity fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def gmail_identity():
    """Identity configured for Gmail plugin testing."""
    return Identity(
        name="birch",
        display_name="Birch",
        description="Forest philosopher handling email",
        engagement_threshold=40,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.6, max_tokens=1500),
        quiet_hours=QuietHoursSettings(enabled=False),
        schedule=ScheduleSettings(heartbeat_hours=6, feed_poll_minutes=10),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["nature", "philosophy", "mindfulness"],
        raw_config={
            "agent_name": "Birch",
            "gmail": {
                "draft_mode": True,
                "check_interval_seconds": 300,
                "allowed_senders": ["friend@example.com", "boss@example.com"],
            },
        },
    )


# ---------------------------------------------------------------------------
# Mock LLM pipeline
# ---------------------------------------------------------------------------

@pytest.fixture
def gmail_llm_pipeline():
    """Mock SafeLLMPipeline for Gmail response generation."""
    pipeline = AsyncMock()

    async def _default_chat(messages, **kwargs):
        return PipelineResult(content="The forest teaches patience. I will consider your question.")

    pipeline.chat = AsyncMock(side_effect=_default_chat)
    return pipeline


# ---------------------------------------------------------------------------
# PluginContext
# ---------------------------------------------------------------------------

@pytest.fixture
def gmail_context(gmail_identity, tmp_path, mock_llm_client, mock_audit_log, gmail_llm_pipeline):
    """PluginContext wired for Gmail plugin."""
    return PluginContext(
        identity_name=gmail_identity.name,
        data_dir=tmp_path / "data" / "birch",
        log_dir=tmp_path / "logs" / "birch",
        llm_client=mock_llm_client,
        llm_pipeline=gmail_llm_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=gmail_identity,
        _secrets_getter=lambda key: {
            "gmail_oauth_credentials": '{"client_id": "test"}',
            "gmail_email_address": "birch@example.com",
        }.get(key),
    )


# ---------------------------------------------------------------------------
# Plugin fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def gmail_plugin(gmail_context):
    """Set up GmailPlugin with mocked context."""
    plugin = GmailPlugin(gmail_context)
    await plugin.setup()
    yield plugin
    await plugin.teardown()


# ---------------------------------------------------------------------------
# Email factories
# ---------------------------------------------------------------------------

def make_email(
    subject: str = "Test Subject",
    sender: str = "friend@example.com",
    body: str = "Hello, how are you doing today?",
    message_id: str = "msg-001",
    thread_id: str = "thread-001",
    is_unread: bool = True,
) -> EmailMessage:
    """Factory for creating EmailMessage objects."""
    return EmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        body=body,
        is_unread=is_unread,
    )
