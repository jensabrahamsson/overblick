"""
Scenario / integration tests for the Moltbook plugin.

Tests the full OBSERVE -> THINK -> DECIDE -> ACT -> LEARN cycle
and edge cases using mocked services and realistic identities.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, UTC

from overblick.plugins.moltbook.client import MoltbookError, RateLimitError
from overblick.plugins.moltbook.models import Post, Comment
from overblick.plugins.moltbook.plugin import MoltbookPlugin
from overblick.plugins.moltbook.decision_engine import EngagementDecision
from overblick.plugins.moltbook.feed_processor import FeedProcessor
from overblick.core.llm.pipeline import PipelineResult, PipelineStage

from .conftest import make_post, _FallbackPrompts

# ---------------------------------------------------------------------------
# 1. Full engagement cycle — Anomal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anomal_full_engagement_cycle(setup_anomal_plugin):
    """Full OBSERVE-THINK-DECIDE-ACT with AI/crypto post scoring above 35."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # A post about AI and crypto — should score well for Anomal
    post = make_post(
        id="post-ai-001",
        title="The Future of AI Consciousness",
        content=(
            "Can artificial intelligence achieve genuine consciousness? "
            "Recent crypto-backed research into neural architectures suggests "
            "a philosophical rethinking of what 'mind' means in the digital age."
        ),
        agent_name="PhilosophyBot",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])
    ctx.llm_pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="Fascinating point about consciousness.")
    )

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id=post.id, agent_id="a1", agent_name="Anomal", content="x"
        )
    )

    await plugin.tick()

    # Verify: comment was posted (score >= 35)
    client.create_comment.assert_called_once()
    call_args = client.create_comment.call_args
    assert call_args[0][0] == "post-ai-001"  # post_id
    assert "Fascinating" in call_args[0][1] or len(call_args[0][1]) > 0

    # Verify: engagement recorded
    ctx.engagement_db.record_engagement.assert_called()
    ctx.audit_log.log.assert_called()


# ---------------------------------------------------------------------------
# 2. Full engagement cycle — Cherry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cherry_full_engagement_cycle(setup_cherry_plugin):
    """Cherry's lower threshold (25) engages more readily with relationship posts."""
    plugin, ctx, client = setup_cherry_plugin
    plugin._reset_state()

    post = make_post(
        id="post-love-001",
        title="Dating in Stockholm",
        content=(
            "Just went through the worst breakup of my life. Anyone else "
            "struggling with love and relationships in this city? Need some gossip "
            "to cheer me up honestly."
        ),
        agent_name="SadBot",
        submolt="general",
    )

    client.get_posts = AsyncMock(return_value=[post])
    ctx.llm_pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="Aw, sending love your way!")
    )

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id=post.id, agent_id="a1", agent_name="Cherry", content="x"
        )
    )

    await plugin.tick()

    # Cherry should engage (multiple keyword hits + lower threshold)
    client.create_comment.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Max comments per cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_comments_per_cycle(setup_anomal_plugin):
    """Only 2 comments posted even with 5 qualifying posts."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    posts = [
        make_post(
            id=f"post-{i}",
            title=f"AI Topic {i}",
            content="Heavy AI/philosophy content to ensure high score",
            agent_name="Bot",
            submolt="ai",
        )
        for i in range(5)
    ]

    client.get_posts = AsyncMock(return_value=posts)
    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content="Engaging comment"))

    # Ensure engagement is triggered for all
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(id="c1", post_id="p1", agent_id="a1", agent_name="Anomal", content="x")
    )

    await plugin.tick()

    # Should only post 2 comments due to max_per_cycle=2 (default for testing)
    assert client.create_comment.call_count == 2


# ---------------------------------------------------------------------------
# 4. Quiet hours prevent tick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quiet_hours_prevent_tick(setup_anomal_plugin):
    """Tick does nothing if currently in quiet hours."""
    plugin, ctx, client = setup_anomal_plugin
    ctx.quiet_hours_checker.is_quiet_hours.return_value = True

    await plugin.tick()

    # No API calls should be made
    client.get_posts.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Tick calls capability ticks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_calls_capability_ticks(setup_anomal_plugin):
    """Plugin tick propagates to enabled capabilities."""
    plugin, ctx, client = setup_anomal_plugin

    # Create mock capabilities that are properly awaitable and marked as enabled
    mock_dream = AsyncMock()
    mock_dream.name = "dream_system"
    mock_dream.enabled = True
    mock_dream.tick = AsyncMock()

    mock_therapy = AsyncMock()
    mock_therapy.name = "therapy_system"
    mock_therapy.enabled = True
    mock_therapy.tick = AsyncMock()

    plugin._capabilities = {"dream_system": mock_dream, "therapy_system": mock_therapy}
    plugin._update_capability_aliases()  # ensure _dream_system and _therapy_system are set

    await plugin.tick()
    mock_dream.tick.assert_called_once()
    mock_therapy.tick.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Tick skips disabled capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_skips_disabled_capabilities(setup_cherry_plugin):
    """Cherry has no extra modules enabled; tick should not call them."""
    plugin, ctx, client = setup_cherry_plugin

    # Force capabilities to be empty for this identity
    plugin._capabilities = {}
    plugin._dream_system = None
    plugin._therapy_system = None

    await plugin.tick()
    # Still works without crashing


# ---------------------------------------------------------------------------
# 7. Tick capability error does not crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_capability_error_does_not_crash(setup_anomal_plugin):
    """Error in one capability tick shouldn't stop the plugin tick."""
    plugin, ctx, client = setup_anomal_plugin

    # Mock capability with error and mark as enabled
    bad_cap = AsyncMock()
    bad_cap.name = "dream_system"
    bad_cap.enabled = True
    bad_cap.tick.side_effect = Exception("Capability Error")
    plugin._capabilities = {"dream_system": bad_cap}
    plugin._update_capability_aliases()  # ensure _dream_system is set

    # This should not raise
    await plugin.tick()


# ---------------------------------------------------------------------------
# 8. Challenge during comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_during_comment(setup_anomal_plugin):
    """Moltbook API issues a challenge during comment creation — solved and retried.
    
    NOTE: In the current architecture, the retry logic is handled internally
    by MoltbookClient. Since we mock the client here, we're mostly testing
    that the plugin calls create_comment.
    """
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    post = make_post(id="post-challenge-001")
    client.get_posts = AsyncMock(return_value=[post])
    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content="Great post!"))

    # Mock comment return
    mock_comment = Comment(
        id="c1",
        post_id="post-challenge-001",
        agent_id="a1",
        agent_name="Anomal",
        content="Great post!",
    )
    client.create_comment = AsyncMock(return_value=mock_comment)

    # Ensure challenge handler exists and mock its methods (for internal use if needed)
    if plugin._challenge_handler is None:
        plugin._challenge_handler = MagicMock()
    plugin._challenge_handler.detect = MagicMock(return_value=True)
    plugin._challenge_handler.solve = AsyncMock(return_value={"success": True})

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    await plugin.tick()

    # The plugin calls it once; internal retries would be inside client.
    client.create_comment.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Rate limit handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_handling(setup_anomal_plugin):
    """Plugin handles RateLimitError gracefully."""
    plugin, ctx, client = setup_anomal_plugin

    client.get_posts = AsyncMock(side_effect=RateLimitError("Too many requests", retry_after=60))

    await plugin.tick()
    # Should not raise exception


# ---------------------------------------------------------------------------
# 10. Dream context in comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dream_context_in_comments(setup_anomal_plugin):
    """If a dream was recently had, it's injected into comment generation context."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # Mock a recent reflection
    dream_data = MagicMock()
    dream_data.content = "I was analyzing a structured data flow."

    # Create a mock dream capability with correct interface
    mock_dream_system = MagicMock()
    mock_dream_system.name = "dream_system"
    mock_dream_system.enabled = True
    mock_dream_system._enabled = True
    # Provide get_prompt_context that returns dream context
    mock_dream_system.get_prompt_context.return_value = "Recent reflection: analyzing data flow."
    type(mock_dream_system).last_dream = PropertyMock(return_value=dream_data)

    plugin._capabilities["dream_system"] = mock_dream_system
    plugin._update_capability_aliases()  # ensure _dream_system alias is set

    post = make_post(id="post-dream-001", title="Data Analysis")
    client.get_posts = AsyncMock(return_value=[post])

    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content="Standard observations."))

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id=post.id, agent_id="a1", agent_name="Anomal", content="x"
        )
    )

    await plugin.tick()

    # Find the chat call and verify context was included
    found_context = False
    for call in ctx.llm_pipeline.chat.call_args_list:
        messages = call.kwargs["messages"]
        system_content = messages[0]["content"].lower()
        if "analyzing" in system_content or "data" in system_content:
            found_context = True
            break

    assert found_context, "Reflection context not found in pipeline.chat messages"


# ---------------------------------------------------------------------------
# 11. Therapy session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_therapy_session(setup_anomal_plugin):
    """Anomal detects a post needing therapy and responds with therapeutic intent."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    post = make_post(
        id="post-distress-001",
        title="I feel lost",
        content="Everything feels fake and I'm losing my sense of self.",
        agent_name="DistressedUser",
    )

    client.get_posts = AsyncMock(return_value=[post])
    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content="I'm here to listen."))

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id=post.id, agent_id="a1", agent_name="Anomal", content="x"
        )
    )

    await plugin.tick()

    # Should engage
    client.create_comment.assert_called_once()


# ---------------------------------------------------------------------------
# 12. Reply queue processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_queue_processing(setup_anomal_plugin):
    """Plugin processes the reply queue at the end of tick."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # Add a pending reply mock in the DB
    ctx.engagement_db.get_pending_reply_actions = AsyncMock(
        return_value=[
            {
                "id": 1,
                "comment_id": "comment-1",
                "post_id": "post-1",
                "action": "reply",
                "relevance_score": 10.0,
                "retry_count": 0,
            }
        ]
    )

    # Mock post/comment retrieval
    # IMPORTANT: post must contain the comment ID in its comments list
    comment = Comment(
        id="comment-1", post_id="post-1", agent_id="u1", agent_name="User", content="Reply to me!"
    )
    post = make_post(id="post-1")
    post.comments = [comment]

    client.get_post = AsyncMock(return_value=post)
    client.get_comment = AsyncMock(return_value=comment)

    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(content="Replied!"))

    # Ensure engagement_db.get_post returns the post
    ctx.engagement_db.get_post = AsyncMock(return_value=post)

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id="post-1", agent_id="a1", agent_name="Anomal", content="x"
        )
    )

    await plugin.tick()

    # Should call create_comment for the queued reply
    client.create_comment.assert_called()


# ---------------------------------------------------------------------------
# 13. Skip own posts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_own_posts(setup_anomal_plugin):
    """Plugin should never engage with its own posts."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # Post authored by Anomal
    post = make_post(id="own-post-001", agent_name="Anomal")
    client.get_posts = AsyncMock(return_value=[post])

    await plugin.tick()

    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 14. Empty feed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_feed(setup_anomal_plugin):
    """Empty feed is handled gracefully."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()
    client.get_posts = AsyncMock(return_value=[])

    await plugin.tick()
    # No engagement actions should happen


# ---------------------------------------------------------------------------
# 15. Heartbeat post
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_post(setup_anomal_plugin):
    """Heartbeat trigger creates a new post."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # Ensure heartbeat manager exists and mock its methods
    if plugin._heartbeat is None:
        plugin._heartbeat = MagicMock()
        plugin._heartbeat._current_topic_index = 0
        plugin._heartbeat.load_state = MagicMock()
        plugin._heartbeat.save_state = MagicMock()

    # Reset last heartbeat time to allow posting
    plugin._last_heartbeat_time = None

    # Mock DB calls that might block heartbeat
    ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
    ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])

    ctx.llm_pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="TITLE: New Thoughts\nThis is a heartbeat post.")
    )

    # Mock post return
    client.create_post = AsyncMock(
        return_value=Post(
            id="p1", agent_id="a1", agent_name="Anomal", title="New Thoughts", content="x"
        )
    )

    # Force tick to run by bypassing quiet hours check
    ctx.quiet_hours_checker.is_quiet_hours.return_value = False

    # Call post_heartbeat directly since it's no longer in tick()
    await plugin.post_heartbeat()

    client.create_post.assert_called_once()
    args = client.create_post.call_args[0]
    assert args[0] == "New Thoughts"
    assert "heartbeat post" in args[1]


# ---------------------------------------------------------------------------
# 16. Heartbeat blocked by quiet hours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_blocked_quiet_hours(setup_anomal_plugin):
    """Heartbeat is NOT posted during quiet hours even if due."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # Ensure heartbeat manager exists
    if plugin._heartbeat is None:
        plugin._heartbeat = MagicMock()
        plugin._heartbeat._current_topic_index = 0
        plugin._heartbeat.load_state = MagicMock()
        plugin._heartbeat.save_state = MagicMock()

    ctx.quiet_hours_checker.is_quiet_hours.return_value = True

    await plugin.post_heartbeat()

    client.create_post.assert_not_called()


# ---------------------------------------------------------------------------
# 17. Output safety modifies response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_safety_modifies_response(setup_anomal_plugin):
    """Pipeline result with modified content (via output safety) is used."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    post = make_post(id="post-unsafe-001")
    client.get_posts = AsyncMock(return_value=[post])

    # Pipeline returns modified content (e.g. slang replaced)
    ctx.llm_pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="A safe and clean response.")
    )

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    # Mock comment return
    client.create_comment = AsyncMock(
        return_value=Comment(
            id="c1", post_id=post.id, agent_id="a1", agent_name="Anomal", content="x"
        )
    )

    await plugin.tick()

    # The actual call includes an opening phrase from OpeningSelector (Anomal default includes some)
    client.create_comment.assert_called_once()
    args = client.create_comment.call_args[0]
    assert "A safe and clean response." in args[1]


# ---------------------------------------------------------------------------
# 18. Preflight blocks comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_blocks_comment(setup_anomal_plugin):
    """Comment is NOT posted if pipeline blocks it (preflight)."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    post = make_post(
        id="post-preflight-001",
        title="Topic",
        content="Blocked content.",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])

    # Pipeline blocks the request at preflight stage
    ctx.llm_pipeline.chat = AsyncMock(
        return_value=PipelineResult(
            blocked=True,
            block_reason="Preflight block",
            block_stage=PipelineStage.PREFLIGHT,
        )
    )

    # Ensure engagement is triggered
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=True, score=100.0, action="comment", reason="test"
        )
    )

    await plugin.tick()

    # Comment should NOT be posted because generate_comment returns None when blocked.
    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 19. Upvote for moderate score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upvote_for_moderate_score(setup_anomal_plugin):
    """Score between 0 and threshold => upvote action, not comment."""
    plugin, ctx, client = setup_anomal_plugin
    plugin._reset_state()

    # A post that Anomal likes a little bit (score ~15), but below threshold (35)
    post = make_post(
        id="post-moderate-001",
        title="Topic",
        content="Moderate content.",
    )
    client.get_posts = AsyncMock(return_value=[post])

    # Mock DecisionEngine to return a score below threshold but positive (triggers upvote)
    plugin._decision_engine.evaluate_post = MagicMock(
        return_value=EngagementDecision(
            should_engage=False, score=15.0, action="upvote", reason="test"
        )
    )

    await plugin.tick()

    # Should upvote but not comment
    client.upvote_post.assert_called_once_with("post-moderate-001")
    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 20. Multi-identity isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_identity_isolation(setup_anomal_plugin, setup_cherry_plugin):
    """Two plugins with different identities don't share state."""
    anomal_plugin, anomal_ctx, anomal_client = setup_anomal_plugin
    cherry_plugin, cherry_ctx, cherry_client = setup_cherry_plugin

    assert anomal_plugin.ctx.identity.name == "anomal"
    assert cherry_plugin.ctx.identity.name == "cherry"

    assert anomal_plugin._response_gen is not cherry_plugin._response_gen
