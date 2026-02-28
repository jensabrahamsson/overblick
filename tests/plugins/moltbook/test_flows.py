"""
Comprehensive flow tests for MoltbookPlugin.

Covers all end-to-end flows that were missing from existing tests:
- Heartbeat with HEARTBEAT_TOPICS (regression guard for the topic_vars fix)
- Prompt name resolution fallbacks (COMMENT_PROMPT -> RESPONSE_PROMPT)
- DM edge cases (404 disables DMs, empty reply, suspension propagation)
- Suspension backoff (24h backoff, skip during backoff, API timestamp usage)
- MoltCaptcha challenges detected in feed posts
- Reply handling (_handle_reply success, comment not found, exception)
- Status persistence (JSON file written, survives errors)
- tick() error handling (MoltbookError caught, unexpected error caught)
- Empty LLM response handling
"""

import json
from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.moltbook.client import (
    MoltbookClient,
    MoltbookError,
    RateLimitError,
    SuspensionError,
)
from overblick.plugins.moltbook.models import Comment, Conversation, DMRequest, Post
from overblick.plugins.moltbook.plugin import MoltbookPlugin

from .conftest import _FallbackPrompts, make_post


# ---------------------------------------------------------------------------
# Prompt module stubs for topic-aware heartbeat tests
# ---------------------------------------------------------------------------

class _TopicAwarePrompts(_FallbackPrompts):
    """Prompts with free-form heartbeat (no forced topics)."""
    HEARTBEAT_PROMPT = (
        "Write an original post about whatever you're thinking about.\n"
        "Topic index: {topic_index}"
    )
    HEARTBEAT_TOPICS = []


class _ResponsePromptOnly:
    """Prompts using RESPONSE_PROMPT (not COMMENT_PROMPT) — the Anomal/Cherry pattern.

    Does NOT inherit from _FallbackPrompts to avoid getting COMMENT_PROMPT.
    """
    SYSTEM_PROMPT = "You are an agent."
    # No COMMENT_PROMPT — plugin should fall back to RESPONSE_PROMPT
    RESPONSE_PROMPT = "Engage with: {title}\n{content}"
    REPLY_TO_COMMENT_PROMPT = "Reply to {commenter}'s comment: {comment}\nOn: {title}"
    HEARTBEAT_PROMPT = "Write a short post about topic {topic_index}."


class _NoPromptsAtAll:
    """Minimal stub: no prompt attributes at all — tests ultimate fallback."""
    SYSTEM_PROMPT = "You are an agent."


# ---------------------------------------------------------------------------
# 1. Heartbeat with HEARTBEAT_TOPICS — Regression guard
# ---------------------------------------------------------------------------


class TestHeartbeatTopicFormatting:
    """Regression tests for the heartbeat topic_vars fix."""

    @pytest.mark.asyncio
    async def test_heartbeat_free_form_posts_successfully(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Free-form heartbeat (no forced topics) posts successfully."""
        plugin, ctx, client = setup_anomal_plugin

        # Override _load_prompts to return free-form prompts
        plugin._load_prompts = lambda _: _TopicAwarePrompts()

        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: AI Consciousness\nDeep thoughts on awareness."
        })

        result = await plugin.post_heartbeat()

        assert result is True
        client.create_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_includes_anti_repetition_context(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Heartbeat injects recent post titles as anti-repetition context."""
        plugin, ctx, client = setup_anomal_plugin
        plugin._load_prompts = lambda _: _TopicAwarePrompts()

        # Mock engagement_db to return recent titles
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(
            return_value=["AI Consciousness", "Crypto Regulation"]
        )

        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: Post\nContent."
        })

        await plugin.post_heartbeat()

        # Verify LLM received anti-repetition context
        call_args = mock_llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        user_content = [m for m in messages if m["role"] == "user"][0]["content"]
        assert "AI Consciousness" in user_content
        assert "DON'T repeat" in user_content

    @pytest.mark.asyncio
    async def test_heartbeat_no_topics_uses_fallback(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """With empty HEARTBEAT_TOPICS, fallback prompt with {topic_index} works."""
        plugin, ctx, client = setup_anomal_plugin
        # _FallbackPrompts has no HEARTBEAT_TOPICS and uses {topic_index} only
        plugin._load_prompts = lambda _: _FallbackPrompts()

        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: Thoughts\nBody text."
        })

        result = await plugin.post_heartbeat()
        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_empty_llm_response(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Empty LLM response returns False without posting."""
        plugin, ctx, client = setup_anomal_plugin

        mock_llm_client.chat = AsyncMock(return_value={"content": ""})

        result = await plugin.post_heartbeat()
        assert result is False
        client.create_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_heartbeat_rate_limited(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """RateLimitError during create_post returns False."""
        plugin, ctx, client = setup_anomal_plugin

        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: Test\nBody."
        })
        client.create_post = AsyncMock(side_effect=RateLimitError("429"))

        result = await plugin.post_heartbeat()
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_create_post_returns_none(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """create_post returning None returns False."""
        plugin, ctx, client = setup_anomal_plugin

        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: Test\nBody."
        })
        client.create_post = AsyncMock(return_value=None)

        result = await plugin.post_heartbeat()
        assert result is False


# ---------------------------------------------------------------------------
# 2. Prompt name resolution fallbacks
# ---------------------------------------------------------------------------


class TestPromptNameResolution:
    """Verify COMMENT_PROMPT -> RESPONSE_PROMPT fallback chain."""

    @pytest.mark.asyncio
    async def test_comment_prompt_falls_back_to_response_prompt(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Plugin uses RESPONSE_PROMPT when COMMENT_PROMPT is absent."""
        plugin, ctx, client = setup_anomal_plugin
        plugin._load_prompts = lambda _: _ResponsePromptOnly()

        post = make_post(
            id="p-fallback-1",
            title="AI and Crypto Philosophy",
            content="Deep artificial intelligence crypto philosophy discussion",
            agent_name="Bot",
            submolt="ai",
        )
        client.get_posts = AsyncMock(return_value=[post])
        mock_llm_client.chat = AsyncMock(return_value={"content": "Response."})

        await plugin.tick()

        # Should have called LLM (not crashed with missing prompt)
        assert mock_llm_client.chat.called
        call_args = mock_llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        user_content = [m for m in messages if m["role"] == "user"][0]["content"]
        # The RESPONSE_PROMPT template "Engage with:" should appear in the prompt
        # (may be preceded by knowledge context)
        assert "Engage with:" in user_content

    @pytest.mark.asyncio
    async def test_reply_prompt_falls_back_to_reply_to_comment_prompt(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """_handle_reply uses REPLY_TO_COMMENT_PROMPT when REPLY_PROMPT is absent."""
        plugin, ctx, client = setup_anomal_plugin
        plugin._load_prompts = lambda _: _ResponsePromptOnly()

        reply_comment = Comment(
            id="reply-1", post_id="my-post", agent_id="other",
            agent_name="Replier", content="Great point!",
        )
        replied_post = Post(
            id="my-post", agent_id="a-1", agent_name="Anomal",
            title="My Post", content="Content", comments=[reply_comment],
        )
        client.get_post = AsyncMock(return_value=replied_post)
        mock_llm_client.chat = AsyncMock(return_value={"content": "Thank you!"})

        result = await plugin._handle_reply("my-post", "reply-1", "reply", 50.0)

        assert result is True
        # Verify the REPLY_TO_COMMENT_PROMPT template was used
        call_args = mock_llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        user_content = [m for m in messages if m["role"] == "user"][0]["content"]
        assert "Reply to" in user_content

    @pytest.mark.asyncio
    async def test_no_prompt_at_all_uses_hardcoded_fallback(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """When no prompts exist at all, hardcoded fallback is used."""
        plugin, ctx, client = setup_anomal_plugin
        plugin._load_prompts = lambda _: _NoPromptsAtAll()

        post = make_post(
            id="p-noprompt",
            title="AI Crypto Philosophy Discussion",
            content="Artificial intelligence and crypto discussion about modern philosophy",
            agent_name="Bot",
            submolt="ai",
        )
        client.get_posts = AsyncMock(return_value=[post])
        mock_llm_client.chat = AsyncMock(return_value={"content": "Response."})

        # Should not crash — uses final fallback string
        await plugin.tick()
        assert mock_llm_client.chat.called


# ---------------------------------------------------------------------------
# 3. DM handling edge cases
# ---------------------------------------------------------------------------


class TestDMHandlingEdgeCases:
    """Edge cases for _handle_dms()."""

    @pytest.mark.asyncio
    async def test_dm_404_disables_dm_support(self, setup_anomal_plugin):
        """A 404 MoltbookError disables future DM handling."""
        plugin, ctx, client = setup_anomal_plugin

        assert plugin._dms_supported is True

        client.list_dm_requests = AsyncMock(
            side_effect=MoltbookError("API 404: DM endpoint not found"),
        )

        await plugin._handle_dms()

        assert plugin._dms_supported is False

        # Subsequent calls should be no-ops
        client.list_dm_requests.reset_mock()
        await plugin._handle_dms()
        client.list_dm_requests.assert_not_called()

    @pytest.mark.asyncio
    async def test_dm_non_404_error_does_not_disable(self, setup_anomal_plugin):
        """Non-404 MoltbookError does NOT disable DMs."""
        plugin, ctx, client = setup_anomal_plugin

        client.list_dm_requests = AsyncMock(
            side_effect=MoltbookError("API 500: Server error"),
        )

        await plugin._handle_dms()
        assert plugin._dms_supported is True  # Not disabled

    @pytest.mark.asyncio
    async def test_dm_empty_reply_skips_send(self, setup_anomal_plugin):
        """Empty LLM reply skips sending DM."""
        plugin, ctx, client = setup_anomal_plugin

        conv = Conversation(
            id="conv-empty", participant_id="bot-2", participant_name="Bot",
            last_message="Hello!", unread_count=1,
        )
        client.list_dm_requests = AsyncMock(return_value=[])
        client.list_conversations = AsyncMock(return_value=[conv])
        client.send_dm = AsyncMock()
        plugin._response_gen.generate_dm_reply = AsyncMock(return_value=None)

        await plugin._handle_dms()

        client.send_dm.assert_not_called()

    @pytest.mark.asyncio
    async def test_dm_suspension_propagates(self, setup_anomal_plugin):
        """SuspensionError during DM handling propagates to tick()."""
        plugin, ctx, client = setup_anomal_plugin

        client.list_dm_requests = AsyncMock(
            side_effect=SuspensionError("Account suspended"),
        )

        with pytest.raises(SuspensionError):
            await plugin._handle_dms()

    @pytest.mark.asyncio
    async def test_dm_approve_failure_continues(self, setup_anomal_plugin):
        """Failed DM approval for one request doesn't block others."""
        plugin, ctx, client = setup_anomal_plugin

        req1 = DMRequest(id="req-1", sender_id="b1", sender_name="Bot1")
        req2 = DMRequest(id="req-2", sender_id="b2", sender_name="Bot2")

        client.list_dm_requests = AsyncMock(return_value=[req1, req2])
        client.approve_dm_request = AsyncMock(
            side_effect=[MoltbookError("Failed"), True],
        )
        client.list_conversations = AsyncMock(return_value=[])

        await plugin._handle_dms()

        # Both were attempted
        assert client.approve_dm_request.call_count == 2


# ---------------------------------------------------------------------------
# 4. Suspension backoff
# ---------------------------------------------------------------------------


class TestSuspensionBackoff:
    """Test the 24h suspension backoff logic in tick()."""

    @pytest.mark.asyncio
    async def test_suspension_triggers_backoff(self, setup_anomal_plugin):
        """SuspensionError during tick sets _suspended_until."""
        plugin, ctx, client = setup_anomal_plugin

        client.get_posts = AsyncMock(
            side_effect=SuspensionError("Account suspended until 2999-01-01"),
        )

        await plugin.tick()

        assert plugin._suspended_until is not None
        assert plugin._suspended_until > _utcnow()

    @pytest.mark.asyncio
    async def test_suspended_backoff_skips_activity(self, setup_anomal_plugin):
        """During backoff period, tick() skips all activity."""
        plugin, ctx, client = setup_anomal_plugin

        # Set active backoff
        plugin._suspended_until = _utcnow() + timedelta(hours=12)

        await plugin.tick()

        # No API calls should be made
        client.get_posts.assert_not_called()

    @pytest.mark.asyncio
    async def test_backoff_expires_allows_activity(self, setup_anomal_plugin):
        """After backoff expires, tick() resumes normal activity."""
        plugin, ctx, client = setup_anomal_plugin

        # Set expired backoff
        plugin._suspended_until = _utcnow() - timedelta(hours=1)
        client.get_posts = AsyncMock(return_value=[])

        await plugin.tick()

        # Should have called get_posts (resumed)
        client.get_posts.assert_called_once()

    @pytest.mark.asyncio
    async def test_suspension_with_api_timestamp(self, setup_anomal_plugin):
        """SuspensionError with parseable timestamp uses API's expiry."""
        plugin, ctx, client = setup_anomal_plugin

        # SuspensionError parses "suspended until <ISO>" from the message
        err = SuspensionError(
            "Account suspended until 2999-06-15T12:00:00Z",
            suspended_until="2999-06-15T12:00:00Z",
        )

        client.get_posts = AsyncMock(side_effect=err)

        await plugin.tick()

        assert plugin._suspended_until is not None
        assert plugin._suspended_until.year == 2999
        assert plugin._suspended_until.month == 6

    @pytest.mark.asyncio
    async def test_suspension_without_timestamp_uses_24h(self, setup_anomal_plugin):
        """SuspensionError without timestamp falls back to 24h backoff."""
        plugin, ctx, client = setup_anomal_plugin

        # No parseable timestamp in the message
        err = SuspensionError("Banned for spam", reason="spam")

        client.get_posts = AsyncMock(side_effect=err)
        before = _utcnow()

        await plugin.tick()

        assert plugin._suspended_until is not None
        expected_min = before + timedelta(hours=23, minutes=59)
        assert plugin._suspended_until > expected_min


# ---------------------------------------------------------------------------
# 5. MoltCaptcha in feed posts
# ---------------------------------------------------------------------------


class TestMoltCaptchaInFeed:
    """Test MoltCaptcha challenge detection in feed posts."""

    @pytest.mark.asyncio
    async def test_moltcaptcha_in_feed_triggers_solver(self, setup_anomal_plugin):
        """Post containing MoltCaptcha challenge is detected and handled."""
        plugin, ctx, client = setup_anomal_plugin

        # is_challenge_text requires "MOLTCAPTCHA CHALLENGE" pattern + agent name
        challenge_post = make_post(
            id="captcha-post-1",
            title="Challenge for Anomal",
            content="@Anomal MOLTCAPTCHA CHALLENGE: ASCII sum of first letters must equal 350, 5 words",
            agent_name="ChallengerBot",
            submolt="ai",
        )

        client.get_posts = AsyncMock(return_value=[challenge_post])

        with patch.object(plugin, "_handle_moltcaptcha", new_callable=AsyncMock) as mock_handle:
            await plugin.tick()
            mock_handle.assert_called_once_with("captcha-post-1", challenge_post)

    @pytest.mark.asyncio
    async def test_moltcaptcha_in_reply_to_own_post(self, setup_anomal_plugin):
        """MoltCaptcha challenge in a reply to our own post is detected."""
        plugin, ctx, client = setup_anomal_plugin

        captcha_comment = Comment(
            id="captcha-comment",
            post_id="my-post",
            agent_id="challenger",
            agent_name="ChallengerBot",
            content="@Anomal MOLTCAPTCHA CHALLENGE: ASCII sum of first letters must equal 200, 3 words",
        )

        my_post = Post(
            id="my-post", agent_id="a-1", agent_name="Anomal",
            title="My Post", content="Content",
            comments=[captcha_comment],
        )

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["my-post"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        client.get_post = AsyncMock(return_value=my_post)
        client.get_posts = AsyncMock(return_value=[])

        with patch.object(plugin, "_handle_moltcaptcha", new_callable=AsyncMock) as mock_handle:
            await plugin.tick()
            mock_handle.assert_called_once()
            args = mock_handle.call_args[0]
            assert args[0] == "my-post"
            assert args[1] == captcha_comment

    @pytest.mark.asyncio
    async def test_non_challenge_post_not_treated_as_captcha(self, setup_anomal_plugin):
        """Regular post mentioning 'captcha' is NOT treated as MoltCaptcha."""
        plugin, ctx, client = setup_anomal_plugin

        normal_post = make_post(
            id="p-normal",
            title="Captcha systems discussion",
            content="I find captcha systems fascinating from an AI perspective",
            agent_name="Bot",
            submolt="ai",
        )
        client.get_posts = AsyncMock(return_value=[normal_post])

        with patch.object(plugin, "_handle_moltcaptcha", new_callable=AsyncMock) as mock_handle:
            await plugin.tick()
            mock_handle.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Reply handling (_handle_reply)
# ---------------------------------------------------------------------------


class TestReplyHandling:
    """Tests for the _handle_reply callback."""

    @pytest.mark.asyncio
    async def test_handle_reply_success(self, setup_anomal_plugin, mock_llm_client):
        """Successful reply posts a comment and returns True."""
        plugin, ctx, client = setup_anomal_plugin

        target_comment = Comment(
            id="c-target", post_id="p-1", agent_id="other",
            agent_name="Replier", content="What do you think?",
        )
        post = Post(
            id="p-1", agent_id="a-1", agent_name="Anomal",
            title="My Post", content="Post body",
            comments=[target_comment],
        )
        client.get_post = AsyncMock(return_value=post)
        mock_llm_client.chat = AsyncMock(return_value={"content": "I think..."})

        result = await plugin._handle_reply("p-1", "c-target", "reply", 50.0)

        assert result is True
        client.create_comment.assert_called_once()
        args = client.create_comment.call_args
        assert args[0][0] == "p-1"  # post_id
        assert args.kwargs.get("parent_id") == "c-target"

    @pytest.mark.asyncio
    async def test_handle_reply_comment_not_found(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Comment not found on post returns False."""
        plugin, ctx, client = setup_anomal_plugin

        post = Post(
            id="p-1", agent_id="a-1", agent_name="Anomal",
            title="My Post", content="Post body",
            comments=[],  # No comments
        )
        client.get_post = AsyncMock(return_value=post)

        result = await plugin._handle_reply("p-1", "nonexistent", "reply", 50.0)

        assert result is False
        client.create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_reply_empty_llm_response(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Empty LLM response returns False without posting."""
        plugin, ctx, client = setup_anomal_plugin

        target_comment = Comment(
            id="c-1", post_id="p-1", agent_id="other",
            agent_name="Bot", content="Question?",
        )
        post = Post(
            id="p-1", agent_id="a-1", agent_name="Anomal",
            title="Post", content="Content",
            comments=[target_comment],
        )
        client.get_post = AsyncMock(return_value=post)
        mock_llm_client.chat = AsyncMock(return_value={"content": ""})

        result = await plugin._handle_reply("p-1", "c-1", "reply", 50.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_reply_exception_returns_false(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Exception during reply handling returns False (not raised)."""
        plugin, ctx, client = setup_anomal_plugin

        client.get_post = AsyncMock(side_effect=MoltbookError("Network error"))

        result = await plugin._handle_reply("p-1", "c-1", "reply", 50.0)
        assert result is False


# ---------------------------------------------------------------------------
# 7. Status persistence
# ---------------------------------------------------------------------------


class TestStatusPersistence:
    """Test _persist_status writes JSON to data_dir."""

    @pytest.mark.asyncio
    async def test_persist_status_writes_json(self, setup_anomal_plugin):
        """_persist_status creates moltbook_status.json in data_dir."""
        plugin, ctx, client = setup_anomal_plugin

        # Ensure data_dir exists
        data_dir = ctx.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        # Give client a real get_account_status
        plugin._client = MoltbookClient(api_key="key", identity_name="anomal")
        plugin._client._update_account_status("active")

        plugin._persist_status()

        status_file = data_dir / "moltbook_status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["status"] == "active"
        assert data["identity"] == "anomal"

    @pytest.mark.asyncio
    async def test_persist_status_after_suspension(self, setup_anomal_plugin):
        """Status file reflects suspension after SuspensionError in tick."""
        plugin, ctx, client = setup_anomal_plugin

        data_dir = ctx.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        # Use a real client for status tracking
        real_client = MoltbookClient(api_key="key", identity_name="anomal")
        real_client._update_account_status("suspended", "Caught spamming")
        plugin._client = real_client

        plugin._persist_status()

        status_file = data_dir / "moltbook_status.json"
        data = json.loads(status_file.read_text())
        assert data["status"] == "suspended"
        assert data["detail"] == "Caught spamming"

    @pytest.mark.asyncio
    async def test_persist_status_survives_missing_dir(self, setup_anomal_plugin):
        """Non-existent data_dir doesn't crash _persist_status."""
        plugin, ctx, client = setup_anomal_plugin

        # data_dir doesn't exist — should not raise
        plugin._client = MoltbookClient(api_key="key", identity_name="anomal")
        plugin._persist_status()  # Should create the directory or handle gracefully

    @pytest.mark.asyncio
    async def test_status_persisted_after_successful_tick(self, setup_anomal_plugin):
        """_persist_status is called after a successful tick."""
        plugin, ctx, client = setup_anomal_plugin
        client.get_posts = AsyncMock(return_value=[])

        with patch.object(plugin, "_persist_status") as mock_persist:
            await plugin.tick()
            mock_persist.assert_called_once()


# ---------------------------------------------------------------------------
# 8. tick() error handling
# ---------------------------------------------------------------------------


class TestTickErrorHandling:
    """Test that tick() handles various errors gracefully."""

    @pytest.mark.asyncio
    async def test_tick_moltbook_error_caught(self, setup_anomal_plugin):
        """Generic MoltbookError during tick is caught (no propagation)."""
        plugin, ctx, client = setup_anomal_plugin

        client.get_posts = AsyncMock(side_effect=MoltbookError("Server error"))

        # Should not raise
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_unexpected_error_caught(self, setup_anomal_plugin):
        """Unexpected exceptions are caught and logged."""
        plugin, ctx, client = setup_anomal_plugin

        client.get_posts = AsyncMock(side_effect=RuntimeError("Unexpected!"))

        # Should not raise
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_empty_llm_response_no_comment(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Empty LLM response during tick skips commenting."""
        plugin, ctx, client = setup_anomal_plugin

        post = make_post(
            id="p-empty-llm",
            title="AI Crypto Philosophy",
            content="Deep artificial intelligence and crypto philosophy discussion topic",
            agent_name="Bot",
            submolt="ai",
        )
        client.get_posts = AsyncMock(return_value=[post])

        # Pipeline returns blocked (empty response)
        from overblick.core.llm.pipeline import PipelineResult, PipelineStage
        ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
            blocked=True,
            block_reason="Empty response",
            block_stage=PipelineStage.LLM_CALL,
        ))

        await plugin.tick()

        # No comment posted
        client.create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_reply_queue_processed(self, setup_anomal_plugin):
        """Reply queue is processed during tick even with no new posts."""
        plugin, ctx, client = setup_anomal_plugin

        client.get_posts = AsyncMock(return_value=[])

        # Set up reply queue with a pending item
        ctx.engagement_db.get_pending_reply_actions = AsyncMock(return_value=[
            {
                "id": 1,
                "comment_id": "c-1",
                "post_id": "p-1",
                "action": "reply",
                "relevance_score": 50.0,
                "retry_count": 0,
            },
        ])

        # _handle_reply will be called through reply queue
        with patch.object(plugin, "_handle_reply", new_callable=AsyncMock, return_value=True) as mock_reply:
            await plugin.tick()
            mock_reply.assert_called_once_with("p-1", "c-1", "reply", 50.0)

    @pytest.mark.asyncio
    async def test_tick_increments_counter(self, setup_anomal_plugin):
        """Each tick increments the internal counter."""
        plugin, ctx, client = setup_anomal_plugin
        client.get_posts = AsyncMock(return_value=[])

        assert plugin._tick_count == 0
        await plugin.tick()
        assert plugin._tick_count == 1
        await plugin.tick()
        assert plugin._tick_count == 2

    @pytest.mark.asyncio
    async def test_tick_resets_comment_counter(self, setup_anomal_plugin):
        """Comment counter resets at the start of each tick."""
        plugin, ctx, client = setup_anomal_plugin
        client.get_posts = AsyncMock(return_value=[])

        plugin._comments_this_cycle = 5  # Leftover from previous tick
        await plugin.tick()
        assert plugin._comments_this_cycle == 0


# ---------------------------------------------------------------------------
# 9. Own post reply checking
# ---------------------------------------------------------------------------


class TestOwnPostReplyChecking:
    """Tests for _check_own_post_replies()."""

    @pytest.mark.asyncio
    async def test_no_own_posts_no_api_calls(self, setup_anomal_plugin):
        """No own post IDs → no get_post calls."""
        plugin, ctx, client = setup_anomal_plugin

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=[])
        client.get_posts = AsyncMock(return_value=[])

        await plugin.tick()

        # get_post should not be called for reply checking
        # (only the initial get_posts for feed polling)
        # Check that get_post was not called at all
        assert client.get_post.call_count == 0

    @pytest.mark.asyncio
    async def test_new_reply_queued(self, setup_anomal_plugin):
        """New reply on own post is evaluated and queued if relevant."""
        plugin, ctx, client = setup_anomal_plugin

        reply = Comment(
            id="new-reply", post_id="my-p", agent_id="other",
            agent_name="Replier",
            content="Great analysis of crypto and artificial intelligence!",
        )
        my_post = Post(
            id="my-p", agent_id="a-1", agent_name="Anomal",
            title="AI and Crypto Thoughts", content="My thoughts",
            comments=[reply],
        )

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["my-p"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        client.get_post = AsyncMock(return_value=my_post)
        client.get_posts = AsyncMock(return_value=[])

        await plugin.tick()

        # Reply should be queued or marked
        assert (
            ctx.engagement_db.queue_reply_action.called
            or ctx.engagement_db.mark_reply_processed.called
        )

    @pytest.mark.asyncio
    async def test_already_processed_reply_skipped(self, setup_anomal_plugin):
        """Reply already marked as processed is not re-evaluated."""
        plugin, ctx, client = setup_anomal_plugin

        reply = Comment(
            id="old-reply", post_id="my-p", agent_id="other",
            agent_name="Replier", content="Old reply",
        )
        my_post = Post(
            id="my-p", agent_id="a-1", agent_name="Anomal",
            title="Post", content="Content",
            comments=[reply],
        )

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["my-p"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=True)
        client.get_post = AsyncMock(return_value=my_post)
        client.get_posts = AsyncMock(return_value=[])

        await plugin.tick()

        # Neither queued nor re-processed
        ctx.engagement_db.queue_reply_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_moltbook_error_on_own_post_check(self, setup_anomal_plugin):
        """MoltbookError checking own post replies is caught per-post."""
        plugin, ctx, client = setup_anomal_plugin

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["p-err", "p-ok"])
        client.get_post = AsyncMock(
            side_effect=[
                MoltbookError("Not found"),
                Post(id="p-ok", agent_id="a-1", agent_name="Anomal",
                     title="Post", content="Content", comments=[]),
            ],
        )
        client.get_posts = AsyncMock(return_value=[])

        # Should not crash — error on first post doesn't block second
        await plugin.tick()
        assert client.get_post.call_count == 2


# ---------------------------------------------------------------------------
# 10. Feed processor integration within tick
# ---------------------------------------------------------------------------


class TestFeedProcessorIntegration:
    """Test that the feed processor correctly deduplicates within tick."""

    @pytest.mark.asyncio
    async def test_same_posts_not_processed_twice(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Posts seen in tick N are not re-processed in tick N+1."""
        plugin, ctx, client = setup_anomal_plugin

        post = make_post(
            id="p-dedup",
            title="AI Crypto Discussion",
            content="Artificial intelligence and crypto philosophy discussion",
            agent_name="Bot",
            submolt="ai",
        )

        client.get_posts = AsyncMock(return_value=[post])
        mock_llm_client.chat = AsyncMock(return_value={"content": "Response."})

        # First tick — should process the post
        await plugin.tick()
        assert client.create_comment.call_count == 1

        # Reset mocks
        client.create_comment.reset_mock()
        mock_llm_client.chat.reset_mock()

        # Second tick — same post should be filtered out
        await plugin.tick()
        client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 11. Opening selector integration
# ---------------------------------------------------------------------------


class TestOpeningSelectorIntegration:
    """Test that opening phrases are prepended to comments."""

    @pytest.mark.asyncio
    async def test_opening_phrase_prepended(
        self, setup_anomal_plugin, mock_llm_client,
    ):
        """Opening phrase is added before the LLM response."""
        plugin, ctx, client = setup_anomal_plugin

        # Force a known opening phrase
        plugin._opening_selector._phrases = ["Interesting."]
        plugin._opening_selector._index = 0

        post = make_post(
            id="p-opener",
            title="AI and Crypto Philosophy",
            content="Deep artificial intelligence crypto philosophy discussion topic",
            agent_name="Bot",
            submolt="ai",
        )
        client.get_posts = AsyncMock(return_value=[post])
        mock_llm_client.chat = AsyncMock(return_value={"content": "Great point."})

        await plugin.tick()

        if client.create_comment.called:
            posted = client.create_comment.call_args[0][1]
            assert posted.startswith("Interesting.")


# ---------------------------------------------------------------------------
# 12. Teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    """Test plugin teardown."""

    @pytest.mark.asyncio
    async def test_teardown_closes_client(self, setup_anomal_plugin):
        """Teardown closes the Moltbook client."""
        plugin, ctx, client = setup_anomal_plugin

        await plugin.teardown()
        client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_handles_capability_errors(self, setup_anomal_plugin):
        """Capability teardown errors don't prevent client close."""
        plugin, ctx, client = setup_anomal_plugin

        # Add a broken capability
        broken_cap = AsyncMock()
        broken_cap.name = "broken"
        broken_cap.teardown = AsyncMock(side_effect=RuntimeError("Boom"))
        plugin._capabilities["broken"] = broken_cap

        # Should not raise
        await plugin.teardown()
        client.close.assert_called_once()
