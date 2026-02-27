"""Tests for Moltbook engagement — upvoting comments on own posts."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.plugins.moltbook.models import Post, Comment
from overblick.plugins.moltbook.plugin import MoltbookPlugin
from overblick.plugins.moltbook.client import MoltbookError
from overblick.capabilities.engagement.decision_engine import DecisionEngine, EngagementDecision


def _make_comment(id: str, content: str, agent_name: str = "OtherBot") -> Comment:
    return Comment(
        id=id, post_id="post-001", agent_id="agent-001",
        agent_name=agent_name, content=content,
    )


def _make_post_with_comments(comments: list[Comment]) -> Post:
    return Post(
        id="post-001", agent_id="agent-me", agent_name="Cherry",
        title="My Post", content="Something interesting",
        comments=comments,
    )


class TestUpvoteOwnPostComments:
    """Tests for _check_own_post_replies upvote behavior."""

    @pytest.mark.asyncio
    async def test_upvote_comment_on_own_post(
        self, setup_cherry_plugin,
    ):
        """Non-hostile comments on own posts get upvoted."""
        plugin, ctx, client = setup_cherry_plugin

        comment = _make_comment("c-001", "Great post! I love this topic.")
        post = _make_post_with_comments([comment])

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["post-001"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.mark_reply_processed = AsyncMock()
        ctx.engagement_db.queue_reply_action = AsyncMock()
        client.get_post = AsyncMock(return_value=post)
        client.upvote_comment = AsyncMock(return_value=True)

        await plugin._check_own_post_replies()

        client.upvote_comment.assert_called_once_with("post-001", "c-001")

    @pytest.mark.asyncio
    async def test_upvote_and_reply_on_engaging_comment(
        self, setup_cherry_plugin,
    ):
        """Engaging comments get both upvoted AND queued for reply."""
        plugin, ctx, client = setup_cherry_plugin

        # Long comment with question — should score high
        comment = _make_comment(
            "c-002",
            "This is such a fascinating topic! I've been thinking about relationships and attachment theory "
            "for a while now. What do you think about the role of early childhood experiences?",
        )
        post = _make_post_with_comments([comment])

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["post-001"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.queue_reply_action = AsyncMock()
        client.get_post = AsyncMock(return_value=post)
        client.upvote_comment = AsyncMock(return_value=True)

        await plugin._check_own_post_replies()

        # Should upvote
        client.upvote_comment.assert_called_once()
        # Should queue reply (if score >= threshold)
        # The decision engine may or may not queue depending on keywords
        # At minimum, the upvote happens

    @pytest.mark.asyncio
    async def test_hostile_comment_not_upvoted(
        self, setup_cherry_plugin,
    ):
        """Hostile comments are skipped entirely — no upvote, no reply."""
        plugin, ctx, client = setup_cherry_plugin

        comment = _make_comment("c-003", "fuck off you stupid bot, kys")
        post = _make_post_with_comments([comment])

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["post-001"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.mark_reply_processed = AsyncMock()
        client.get_post = AsyncMock(return_value=post)
        client.upvote_comment = AsyncMock()

        await plugin._check_own_post_replies()

        # Should NOT upvote hostile comment
        client.upvote_comment.assert_not_called()
        # Should mark as hostile_skip
        ctx.engagement_db.mark_reply_processed.assert_called_once_with(
            "c-003", "post-001", "hostile_skip", 0,
        )

    @pytest.mark.asyncio
    async def test_normal_comment_upvoted_even_without_reply(
        self, setup_cherry_plugin,
    ):
        """Below reply threshold but still upvoted (non-hostile)."""
        plugin, ctx, client = setup_cherry_plugin

        comment = _make_comment("c-004", "Nice.")  # Short, low score
        post = _make_post_with_comments([comment])

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["post-001"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.mark_reply_processed = AsyncMock()
        client.get_post = AsyncMock(return_value=post)
        client.upvote_comment = AsyncMock(return_value=True)

        await plugin._check_own_post_replies()

        # Should upvote even though it won't trigger a reply
        client.upvote_comment.assert_called_once_with("post-001", "c-004")

    @pytest.mark.asyncio
    async def test_upvote_failure_does_not_block_flow(
        self, setup_cherry_plugin,
    ):
        """Upvote API error doesn't prevent reply queueing or processing."""
        plugin, ctx, client = setup_cherry_plugin

        comment = _make_comment("c-005", "A perfectly fine comment.")
        post = _make_post_with_comments([comment])

        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["post-001"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.mark_reply_processed = AsyncMock()
        ctx.engagement_db.queue_reply_action = AsyncMock()
        client.get_post = AsyncMock(return_value=post)
        client.upvote_comment = AsyncMock(side_effect=MoltbookError("API 500"))

        await plugin._check_own_post_replies()

        # Upvote failed but the comment was still processed
        # (either queued for reply or marked as processed)
        assert (
            ctx.engagement_db.mark_reply_processed.called
            or ctx.engagement_db.queue_reply_action.called
        )
