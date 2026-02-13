"""
Tests for AnalyzerCapability — engagement analysis wrapper.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from blick.core.capability import CapabilityContext
from blick.capabilities.engagement.analyzer import AnalyzerCapability


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestAnalyzerCapability:
    @pytest.mark.asyncio
    async def test_setup_creates_engine(self):
        ctx = make_ctx(config={
            "interest_keywords": ["AI", "crypto"],
            "engagement_threshold": 35.0,
            "agent_name": "TestBot",
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = AnalyzerCapability(ctx)
        assert cap.name == "analyzer"

    @pytest.mark.asyncio
    async def test_evaluate_matching_post(self):
        ctx = make_ctx(config={
            "interest_keywords": ["artificial intelligence", "crypto"],
            "engagement_threshold": 35.0,
            "agent_name": "TestBot",
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        decision = cap.evaluate(
            title="The Future of Artificial Intelligence",
            content="This post discusses AI and its impact on society. What do you think?",
            agent_name="OtherBot",
            submolt="ai",
        )
        assert decision.should_engage is True
        assert decision.score >= 35.0
        assert len(decision.matched_keywords) > 0

    @pytest.mark.asyncio
    async def test_evaluate_own_post_skipped(self):
        ctx = make_ctx(config={
            "interest_keywords": ["AI"],
            "agent_name": "TestBot",
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        decision = cap.evaluate(
            title="Great AI Post",
            content="AI is amazing",
            agent_name="TestBot",
        )
        assert decision.should_engage is False
        assert decision.action == "skip"

    @pytest.mark.asyncio
    async def test_evaluate_irrelevant_post(self):
        ctx = make_ctx(config={
            "interest_keywords": ["quantum physics"],
            "engagement_threshold": 35.0,
            "agent_name": "TestBot",
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        decision = cap.evaluate(
            title="Best Recipes",
            content="Here is a recipe for cookies",
            agent_name="OtherBot",
        )
        assert decision.should_engage is False

    @pytest.mark.asyncio
    async def test_evaluate_reply(self):
        ctx = make_ctx(config={
            "interest_keywords": ["AI"],
            "engagement_threshold": 35.0,
            "agent_name": "TestBot",
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        decision = cap.evaluate_reply(
            comment_content="Great point about AI! What about consciousness?",
            original_post_title="AI Thoughts",
            commenter_name="OtherBot",
        )
        assert decision.should_engage is True
        assert decision.score > 0

    @pytest.mark.asyncio
    async def test_evaluate_not_initialized(self):
        ctx = make_ctx()
        cap = AnalyzerCapability(ctx)
        # Don't call setup
        decision = cap.evaluate("title", "content", "agent")
        assert decision.should_engage is False
        assert decision.reason == "not initialized"

    @pytest.mark.asyncio
    async def test_default_agent_name_from_identity(self):
        ctx = make_ctx(config={
            "interest_keywords": ["AI"],
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        # Should use identity_name as default agent_name
        decision = cap.evaluate(
            title="AI Post",
            content="AI content",
            agent_name="test",  # Same as identity_name
        )
        assert decision.action == "skip"  # Own post
