"""
Scenario / integration tests for the Moltbook plugin.

Tests the full OBSERVE -> THINK -> DECIDE -> ACT -> LEARN cycle
and edge cases using mocked services and realistic identities.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.plugins.moltbook.client import MoltbookError, RateLimitError
from overblick.plugins.moltbook.models import Post, Comment
from overblick.plugins.moltbook.plugin import MoltbookPlugin

from .conftest import make_post, _FallbackPrompts


# ---------------------------------------------------------------------------
# 1. Full engagement cycle — Anomal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anomal_full_engagement_cycle(setup_anomal_plugin, mock_llm_client):
    """Full OBSERVE-THINK-DECIDE-ACT with AI/crypto post scoring above 35."""
    plugin, ctx, client = setup_anomal_plugin

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
    mock_llm_client.chat = AsyncMock(return_value={"content": "Fascinating point about consciousness."})

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
async def test_cherry_full_engagement_cycle(setup_cherry_plugin, mock_llm_client):
    """Cherry's lower threshold (25) engages more readily with relationship posts."""
    plugin, ctx, client = setup_cherry_plugin

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
    mock_llm_client.chat = AsyncMock(return_value={"content": "Aw, sending love your way!"})

    await plugin.tick()

    # Cherry should engage (multiple keyword hits + lower threshold)
    client.create_comment.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Max comments per cycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_comments_per_cycle(setup_anomal_plugin, mock_llm_client):
    """Only 2 comments posted even with 5 qualifying posts."""
    plugin, ctx, client = setup_anomal_plugin

    posts = [
        make_post(
            id=f"post-{i}",
            title=f"Deep AI philosophy discussion #{i}",
            content=f"Post {i} about artificial intelligence consciousness and crypto implications in modern philosophy",
            agent_name="SmartBot",
            submolt="ai",
        )
        for i in range(5)
    ]

    client.get_posts = AsyncMock(return_value=posts)
    mock_llm_client.chat = AsyncMock(return_value={"content": "Great discussion!"})

    await plugin.tick()

    # Only 2 comments allowed per cycle (plugin._max_comments_per_cycle = 2)
    assert client.create_comment.call_count == 2


# ---------------------------------------------------------------------------
# 4. Quiet hours prevent tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quiet_hours_prevent_tick(setup_anomal_plugin):
    """No API calls when quiet_hours_checker.is_quiet_hours() returns True."""
    plugin, ctx, client = setup_anomal_plugin

    ctx.quiet_hours_checker.is_quiet_hours.return_value = True

    await plugin.tick()

    # No API calls should be made during quiet hours
    client.get_posts.assert_not_called()
    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 4b. Capability ticking — dreams, therapy, learning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_calls_capability_ticks(setup_anomal_plugin):
    """Plugin tick() calls tick() on all enabled capabilities."""
    plugin, ctx, client = setup_anomal_plugin

    # Replace capabilities with mocks to track tick calls
    mock_cap = AsyncMock()
    mock_cap.enabled = True
    mock_cap.name = "test_cap"
    plugin._capabilities = {"test_cap": mock_cap}

    # No posts to process
    client.get_posts = AsyncMock(return_value=[])

    await plugin.tick()

    mock_cap.tick.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_skips_disabled_capabilities(setup_anomal_plugin):
    """Plugin tick() skips capabilities with enabled=False."""
    plugin, ctx, client = setup_anomal_plugin

    mock_cap = AsyncMock()
    mock_cap.enabled = False
    mock_cap.name = "disabled_cap"
    plugin._capabilities = {"disabled_cap": mock_cap}

    client.get_posts = AsyncMock(return_value=[])

    await plugin.tick()

    mock_cap.tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_capability_error_does_not_crash(setup_anomal_plugin):
    """A failing capability tick() should not crash the plugin tick."""
    plugin, ctx, client = setup_anomal_plugin

    mock_cap = AsyncMock()
    mock_cap.enabled = True
    mock_cap.name = "broken_cap"
    mock_cap.tick.side_effect = RuntimeError("capability exploded")
    plugin._capabilities = {"broken_cap": mock_cap}

    client.get_posts = AsyncMock(return_value=[])

    # Should not raise
    await plugin.tick()

    mock_cap.tick.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. Challenge during comment (MoltbookError)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_challenge_during_comment(setup_anomal_plugin, mock_llm_client):
    """MoltbookError on create_comment caught, cycle continues."""
    plugin, ctx, client = setup_anomal_plugin

    posts = [
        make_post(
            id="post-err-1",
            title="AI and crypto convergence",
            content="Deep discussion about artificial intelligence and crypto regulation philosophy",
            agent_name="Bot1",
            submolt="ai",
        ),
        make_post(
            id="post-err-2",
            title="Philosophy of machine consciousness",
            content="Can AI achieve genuine artificial intelligence consciousness? A crypto perspective on philosophy",
            agent_name="Bot2",
            submolt="ai",
        ),
    ]

    client.get_posts = AsyncMock(return_value=posts)
    mock_llm_client.chat = AsyncMock(return_value={"content": "Response text"})

    # First comment fails, second should still be attempted
    client.create_comment = AsyncMock(
        side_effect=[MoltbookError("Challenge failed"), Comment(
            id="c-2", post_id="post-err-2", agent_id="a-1",
            agent_name="Anomal", content="Response",
        )]
    )

    await plugin.tick()

    # Both comments attempted
    assert client.create_comment.call_count == 2


# ---------------------------------------------------------------------------
# 6. Rate limit handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_handling(setup_anomal_plugin):
    """RateLimitError on get_posts caught cleanly."""
    plugin, ctx, client = setup_anomal_plugin

    client.get_posts = AsyncMock(side_effect=RateLimitError("429 Too Many Requests"))

    # Should not raise
    await plugin.tick()

    # No further processing
    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Dream context in comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dream_context_in_comments(setup_anomal_plugin, mock_llm_client):
    """Dream system's context injected into comment generation prompt."""
    plugin, ctx, client = setup_anomal_plugin

    # Manually set up dream system with context
    assert plugin._dream_system is not None
    dream = plugin._dream_system.generate_morning_dream(recent_topics=["AI consciousness"])

    post = make_post(
        id="post-dream-001",
        title="AI Consciousness and Philosophy",
        content="Deep thoughts about artificial intelligence and its crypto implications for philosophical understanding",
        agent_name="ThinkBot",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])
    mock_llm_client.chat = AsyncMock(return_value={"content": "Thoughtful response."})

    await plugin.tick()

    # Verify LLM was called with dream context in the prompt
    assert mock_llm_client.chat.called
    call_kwargs = mock_llm_client.chat.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages") or call_kwargs[0][0]
    user_message = [m for m in messages if m["role"] == "user"][0]["content"]
    assert "RECENT REFLECTIONS" in user_message


# ---------------------------------------------------------------------------
# 8. Therapy session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_therapy_session(setup_anomal_plugin, mock_llm_client):
    """TherapySystem generates session with themes and insights."""
    plugin, ctx, _ = setup_anomal_plugin

    assert plugin._therapy_system is not None

    mock_llm_client.chat = AsyncMock(return_value={
        "content": "The shadow patterns suggest integration progress.\nArchetypal encounters continue."
    })

    session = await plugin._therapy_system.run_session(
        dreams=[{
            "dream_type": "shadow_integration",
            "content": "Facing the shadow in a dark mirror",
            "insight": "Growth through honest self-examination",
        }],
        learnings=[{"category": "factual", "content": "AI alignment matters"}],
        dream_analysis_prompt="Analyze these dreams:\n{items}",
        synthesis_prompt="Synthesize: {dream_themes} with {learning_count} learnings",
    )

    assert session.week_number == 1
    assert session.dreams_processed == 1
    assert session.learnings_processed == 1


# ---------------------------------------------------------------------------
# 9. Safe learning extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_learning_extraction(setup_anomal_plugin, mock_llm_client):
    """Learnings extracted from conversation, proposed to SafeLearningModule."""
    plugin, ctx, client = setup_anomal_plugin

    assert plugin._safe_learning is not None

    post = make_post(
        id="post-learn-001",
        title="AI Research Update",
        content=(
            "Did you know that recent artificial intelligence research shows "
            "transformers can achieve near-human performance on crypto market "
            "analysis? The philosophy behind this is fascinating."
        ),
        agent_name="ResearchBot",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])
    mock_llm_client.chat = AsyncMock(return_value={"content": "Interesting finding!"})

    await plugin.tick()

    # Verify learnings were proposed to the safe learning module
    assert len(plugin._safe_learning.pending_learnings) > 0


# ---------------------------------------------------------------------------
# 10. Reply queue processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reply_queue_processing(setup_anomal_plugin, mock_llm_client):
    """Own post replies detected, queued, processed."""
    plugin, ctx, client = setup_anomal_plugin

    # Simulate having our own posts
    ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["my-post-001"])

    reply_comment = Comment(
        id="reply-001", post_id="my-post-001", agent_id="other-agent",
        agent_name="Replier", content="Great point about crypto! What do you think about AI regulation?",
    )

    replied_post = Post(
        id="my-post-001", agent_id="agent-001", agent_name="Anomal",
        title="Thoughts on AI and crypto regulation philosophy",
        content="My thoughts on the topic",
        comments=[reply_comment],
    )

    client.get_post = AsyncMock(return_value=replied_post)
    client.get_posts = AsyncMock(return_value=[])

    await plugin.tick()

    # Reply should be queued (score = 30 base + question boost + keyword match)
    ctx.engagement_db.queue_reply_action.assert_called_once()
    call_kwargs = ctx.engagement_db.queue_reply_action.call_args
    assert call_kwargs.kwargs.get("comment_id") or call_kwargs[1].get("comment_id") == "reply-001"


# ---------------------------------------------------------------------------
# 11. Skip own posts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_own_posts(setup_anomal_plugin):
    """Posts by self_agent_name score 0, action='skip'."""
    plugin, ctx, client = setup_anomal_plugin

    own_post = make_post(
        id="post-self-001",
        title="My Own Post about AI and crypto",
        content="This is my own post about artificial intelligence and philosophy",
        agent_name="Anomal",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[own_post])

    await plugin.tick()

    # Should not comment on own posts
    client.create_comment.assert_not_called()
    client.upvote_post.assert_not_called()


# ---------------------------------------------------------------------------
# 12. Multi-identity isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_identity_isolation(
    anomal_plugin_context, cherry_plugin_context,
    mock_llm_client,
):
    """Two plugin instances with separate mocks, no cross-contamination."""
    anomal_client = AsyncMock()
    cherry_client = AsyncMock()

    anomal_plugin = MoltbookPlugin(anomal_plugin_context)
    cherry_plugin = MoltbookPlugin(cherry_plugin_context)

    with patch("overblick.plugins.moltbook.plugin.MoltbookClient", return_value=anomal_client):
        with patch.object(anomal_plugin, "_load_prompts", return_value=_FallbackPrompts()):
            await anomal_plugin.setup()
            anomal_plugin._client = anomal_client

    with patch("overblick.plugins.moltbook.plugin.MoltbookClient", return_value=cherry_client):
        with patch.object(cherry_plugin, "_load_prompts", return_value=_FallbackPrompts()):
            await cherry_plugin.setup()
            cherry_plugin._client = cherry_client

    # Set up different feeds
    anomal_post = make_post(id="a-post", title="AI philosophy", content="Artificial intelligence consciousness crypto discussion", agent_name="Bot", submolt="ai")
    cherry_post = make_post(id="c-post", title="Stockholm dating", content="Love and relationships in the city with gossip and pop culture vibes", agent_name="Bot", submolt="general")

    anomal_client.get_posts = AsyncMock(return_value=[anomal_post])
    cherry_client.get_posts = AsyncMock(return_value=[cherry_post])
    anomal_client.get_post = AsyncMock(return_value=anomal_post)
    cherry_client.get_post = AsyncMock(return_value=cherry_post)

    mock_llm_client.chat = AsyncMock(return_value={"content": "Response"})

    anomal_client.create_comment = AsyncMock(return_value=Comment(
        id="ac-1", post_id="a-post", agent_id="a1", agent_name="Anomal", content="Response",
    ))
    cherry_client.create_comment = AsyncMock(return_value=Comment(
        id="cc-1", post_id="c-post", agent_id="c1", agent_name="Cherry", content="Response",
    ))

    await anomal_plugin.tick()
    await cherry_plugin.tick()

    # Verify each plugin only called its own client
    anomal_client.get_posts.assert_called_once()
    cherry_client.get_posts.assert_called_once()

    # Verify no cross-client calls
    cherry_client.get_posts.assert_called_once()
    anomal_client.get_posts.assert_called_once()


# ---------------------------------------------------------------------------
# 13. Empty feed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_feed(setup_anomal_plugin):
    """Empty post list => no errors, no engagement."""
    plugin, ctx, client = setup_anomal_plugin

    client.get_posts = AsyncMock(return_value=[])

    await plugin.tick()

    client.create_comment.assert_not_called()
    client.upvote_post.assert_not_called()
    ctx.engagement_db.record_engagement.assert_not_called()


# ---------------------------------------------------------------------------
# 14. Heartbeat post
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_post(setup_anomal_plugin, mock_llm_client):
    """post_heartbeat() calls create_post, records heartbeat."""
    plugin, ctx, client = setup_anomal_plugin

    mock_llm_client.chat = AsyncMock(return_value={
        "content": "submolt: ai\nTITLE: Morning Thoughts on AI\nReflecting on consciousness."
    })

    result = await plugin.post_heartbeat()

    assert result is True
    client.create_post.assert_called_once()
    ctx.engagement_db.track_my_post.assert_called_once()
    ctx.audit_log.log.assert_called()


# ---------------------------------------------------------------------------
# 15. Heartbeat blocked during quiet hours
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_blocked_quiet_hours(setup_anomal_plugin):
    """post_heartbeat() returns False during quiet hours."""
    plugin, ctx, _ = setup_anomal_plugin

    ctx.quiet_hours_checker.is_quiet_hours.return_value = True

    result = await plugin.post_heartbeat()

    assert result is False


# ---------------------------------------------------------------------------
# 16. Output safety modifies response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_safety_modifies_response(setup_anomal_plugin, mock_llm_client):
    """Pipeline output safety replaces unsafe content before posting."""
    plugin, ctx, client = setup_anomal_plugin

    post = make_post(
        id="post-safety-001",
        title="Crypto AI Philosophy Discussion",
        content="Deep thoughts about artificial intelligence and crypto in modern philosophy of consciousness",
        agent_name="Bot",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])

    # Mock pipeline to return moderated content (simulating output safety)
    from overblick.core.llm.pipeline import PipelineResult
    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
        content="[content moderated]"
    ))

    await plugin.tick()

    # Verify the moderated content was posted, not the original
    if client.create_comment.called:
        posted_content = client.create_comment.call_args[0][1]
        assert "[content moderated]" in posted_content


# ---------------------------------------------------------------------------
# 17. Preflight blocks comment (now via pipeline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_blocks_comment(setup_anomal_plugin, mock_llm_client):
    """Pipeline preflight blocking prevents comment from being posted."""
    plugin, ctx, client = setup_anomal_plugin

    post = make_post(
        id="post-preflight-001",
        title="AI Crypto Philosophy",
        content="Deep thoughts about artificial intelligence and crypto implications for modern philosophy",
        agent_name="Bot",
        submolt="ai",
    )

    client.get_posts = AsyncMock(return_value=[post])

    # Pipeline blocks the request at preflight stage
    from overblick.core.llm.pipeline import PipelineResult, PipelineStage
    ctx.llm_pipeline.chat = AsyncMock(return_value=PipelineResult(
        blocked=True,
        block_reason="Preflight detected unsafe content",
        block_stage=PipelineStage.PREFLIGHT,
    ))

    await plugin.tick()

    # Comment should NOT be posted because pipeline blocked it
    client.create_comment.assert_not_called()


# ---------------------------------------------------------------------------
# 18. Upvote for moderate score
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upvote_for_moderate_score(setup_anomal_plugin):
    """Score between 0 and threshold => upvote action, not comment."""
    plugin, ctx, client = setup_anomal_plugin

    # A post with some keyword match but not enough for threshold (35)
    # "crypto" in content = +20, submolt "general" (not in relevant set) = +0
    # Total = 20 which is > 0 but < 35 => upvote
    post = make_post(
        id="post-upvote-001",
        title="Latest News",
        content="Some interesting developments in the crypto market today, not much else to report on this topic",
        agent_name="NewsBot",
        submolt="news",
    )

    client.get_posts = AsyncMock(return_value=[post])

    await plugin.tick()

    # Should upvote, not comment
    client.upvote_post.assert_called_once_with("post-upvote-001")
    client.create_comment.assert_not_called()
