"""
Shared fixtures for Moltbook plugin scenario tests.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.personalities import Identity, LLMSettings, QuietHoursSettings, ScheduleSettings, SecuritySettings
from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.core.plugin_base import PluginContext
from overblick.plugins.moltbook.models import Post, Comment
from overblick.plugins.moltbook.plugin import MoltbookPlugin


# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anomal_identity():
    """Realistic Anomal identity for scenario tests."""
    return Identity(
        name="anomal",
        display_name="Anomal",
        description="Intellectual humanist exploring AI's role in society",
        engagement_threshold=35,
        enabled_modules=("dream_system", "therapy_system", "safe_learning"),
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["artificial intelligence", "crypto", "philosophy", "jung"],
        raw_config={
            "agent_name": "Anomal",
            "engagement_threshold": 35.0,
            "interest_keywords": ["artificial intelligence", "crypto", "philosophy", "jung"],
            "enabled_modules": ["dream_system", "therapy_system", "safe_learning"],
        },
    )


@pytest.fixture
def cherry_identity():
    """Realistic Cherry identity for scenario tests."""
    return Identity(
        name="cherry",
        display_name="Cherry",
        description="Flirty Swedish woman exploring life, love, and pop culture",
        engagement_threshold=25,
        enabled_modules=(),
        llm=LLMSettings(model="qwen3:8b", temperature=0.8, max_tokens=2000),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=22, end_hour=8),
        schedule=ScheduleSettings(heartbeat_hours=3, feed_poll_minutes=4),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        interest_keywords=["dating", "relationships", "love", "gossip", "pop culture"],
        raw_config={
            "agent_name": "Cherry",
            "engagement_threshold": 25.0,
            "interest_keywords": ["dating", "relationships", "love", "gossip", "pop culture"],
            "enabled_modules": [],
        },
    )


# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_moltbook_client():
    """AsyncMock Moltbook client with all API methods."""
    client = AsyncMock()
    client.get_posts = AsyncMock(return_value=[])
    client.create_comment = AsyncMock(return_value=Comment(
        id="comment-001", post_id="post-001", agent_id="agent-001",
        agent_name="Anomal", content="Mock comment",
    ))
    client.upvote_post = AsyncMock(return_value=True)
    client.get_post = AsyncMock(return_value=Post(
        id="post-001", agent_id="agent-001", agent_name="OtherBot",
        title="Test Post", content="Test content",
    ))
    client.create_post = AsyncMock(return_value=Post(
        id="post-new", agent_id="agent-001", agent_name="Anomal",
        title="Heartbeat Title", content="Heartbeat body",
    ))
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_quiet_hours_checker():
    """Mock quiet hours checker (not quiet by default)."""
    checker = MagicMock()
    checker.is_quiet_hours = MagicMock(return_value=False)
    return checker


@pytest.fixture
def mock_preflight_checker():
    """Mock preflight checker (passes by default)."""
    checker = AsyncMock()
    checker.check = AsyncMock(return_value=True)
    return checker


@pytest.fixture
def mock_output_safety():
    """Mock output safety (returns input unchanged)."""
    safety = AsyncMock()
    safety.check = AsyncMock(side_effect=lambda text: text)
    return safety


@pytest.fixture
def mock_llm_pipeline(mock_llm_client):
    """
    Mock SafeLLMPipeline that passes through to the mock LLM client.

    By default, returns the mock LLM response through a PipelineResult.
    Tests can override pipeline.chat to simulate blocking behavior.
    """
    pipeline = AsyncMock()

    async def _pipeline_chat(messages, **kwargs):
        """Default: pass through to mock LLM client."""
        result = await mock_llm_client.chat(
            messages=messages,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
        )
        if result and result.get("content"):
            return PipelineResult(content=result["content"].strip())
        return PipelineResult(blocked=True, block_reason="Empty LLM response",
                              block_stage=PipelineStage.LLM_CALL)

    pipeline.chat = AsyncMock(side_effect=_pipeline_chat)
    return pipeline


# ---------------------------------------------------------------------------
# PluginContext fixtures
# ---------------------------------------------------------------------------

def _make_plugin_context(
    identity, tmp_path, mock_llm_client, mock_audit_log, mock_engagement_db,
    mock_quiet_hours_checker, mock_preflight_checker, mock_output_safety,
    mock_llm_pipeline=None,
):
    """Build a PluginContext with all mocks wired."""
    ctx = PluginContext(
        identity_name=identity.name,
        data_dir=tmp_path / "data" / identity.name,
        log_dir=tmp_path / "logs" / identity.name,
        llm_client=mock_llm_client,
        llm_pipeline=mock_llm_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=mock_quiet_hours_checker,
        identity=identity,
        engagement_db=mock_engagement_db,
        preflight_checker=mock_preflight_checker,
        output_safety=mock_output_safety,
    )
    ctx._secrets_getter = lambda key: {"moltbook_api_key": "test-key", "moltbook_agent_id": "agent-001"}.get(key)
    return ctx


@pytest.fixture
def anomal_plugin_context(
    anomal_identity, tmp_path, mock_llm_client, mock_audit_log,
    mock_engagement_db, mock_quiet_hours_checker, mock_preflight_checker,
    mock_output_safety, mock_llm_pipeline,
):
    """PluginContext wired for Anomal identity."""
    return _make_plugin_context(
        anomal_identity, tmp_path, mock_llm_client, mock_audit_log,
        mock_engagement_db, mock_quiet_hours_checker, mock_preflight_checker,
        mock_output_safety, mock_llm_pipeline,
    )


@pytest.fixture
def cherry_plugin_context(
    cherry_identity, tmp_path, mock_llm_client, mock_audit_log,
    mock_engagement_db, mock_quiet_hours_checker, mock_preflight_checker,
    mock_output_safety, mock_llm_pipeline,
):
    """PluginContext wired for Cherry identity."""
    return _make_plugin_context(
        cherry_identity, tmp_path, mock_llm_client, mock_audit_log,
        mock_engagement_db, mock_quiet_hours_checker, mock_preflight_checker,
        mock_output_safety, mock_llm_pipeline,
    )


# ---------------------------------------------------------------------------
# Post factory
# ---------------------------------------------------------------------------

def make_post(
    id: str = "post-001",
    title: str = "Test Post",
    content: str = "Test content with enough text to pass length checks",
    agent_name: str = "OtherBot",
    submolt: str = "ai",
    comments: list | None = None,
) -> Post:
    """Factory for creating Post objects in tests."""
    post = Post(
        id=id,
        agent_id=f"agent-{id}",
        agent_name=agent_name,
        title=title,
        content=content,
        comments=comments or [],
    )
    # submolt is accessed via getattr(post, "submolt", "") in plugin.py
    post.submolt = submolt
    return post


# ---------------------------------------------------------------------------
# Plugin setup fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def setup_anomal_plugin(anomal_plugin_context, mock_moltbook_client):
    """Create and setup a MoltbookPlugin for Anomal with mocked client."""
    plugin = MoltbookPlugin(anomal_plugin_context)

    with patch("overblick.plugins.moltbook.plugin.MoltbookClient", return_value=mock_moltbook_client):
        with patch.object(plugin, "_load_prompts", return_value=_FallbackPrompts()):
            await plugin.setup()
            # Replace client with our mock
            plugin._client = mock_moltbook_client
            yield plugin, anomal_plugin_context, mock_moltbook_client


@pytest.fixture
async def setup_cherry_plugin(cherry_plugin_context, mock_moltbook_client):
    """Create and setup a MoltbookPlugin for Cherry with mocked client."""
    plugin = MoltbookPlugin(cherry_plugin_context)

    with patch("overblick.plugins.moltbook.plugin.MoltbookClient", return_value=mock_moltbook_client):
        with patch.object(plugin, "_load_prompts", return_value=_FallbackPrompts()):
            await plugin.setup()
            plugin._client = mock_moltbook_client
            yield plugin, cherry_plugin_context, mock_moltbook_client


class _FallbackPrompts:
    """Test-only fallback prompts."""
    SYSTEM_PROMPT = "You are a test AI agent."
    COMMENT_PROMPT = "Respond to this post:\nTitle: {title}\n{content}"
    REPLY_PROMPT = "Reply to: {comment}\nOn post: {title}"
    HEARTBEAT_PROMPT = "Write a short post about topic {topic_index}."
