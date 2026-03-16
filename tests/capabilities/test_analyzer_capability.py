"""Tests for AnalyzerCapability — engagement analysis wrapper.

Covers uncovered line 72: evaluate_reply returns not-initialized when no engine.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overblick.capabilities.engagement.analyzer import AnalyzerCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestAnalyzerCapabilityCoverage:
    @pytest.mark.asyncio
    async def test_evaluate_reply_not_initialized(self):
        """Cover line 72: evaluate_reply when engine is None."""
        ctx = make_ctx()
        cap = AnalyzerCapability(ctx)
        # Don't call setup

        result = cap.evaluate_reply(
            comment_content="Interesting!",
            original_post_title="Test Post",
            commenter_name="Someone",
        )
        assert result.should_engage is False
        assert result.score == 0.0
        assert result.action == "skip"
        assert result.reason == "not initialized"

    @pytest.mark.asyncio
    async def test_evaluate_not_initialized(self):
        """evaluate returns not-initialized when engine is None."""
        ctx = make_ctx()
        cap = AnalyzerCapability(ctx)

        result = cap.evaluate("Title", "Content", "Agent")
        assert result.should_engage is False
        assert result.reason == "not initialized"

    @pytest.mark.asyncio
    async def test_setup_and_evaluate(self):
        """Full lifecycle: setup then evaluate."""
        ctx = make_ctx(config={
            "interest_keywords": ["ai", "crypto"],
            "engagement_threshold": 20.0,
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        result = cap.evaluate("AI Discussion", "AI is great", "Bot", "ai")
        assert isinstance(result.score, float)

    @pytest.mark.asyncio
    async def test_setup_and_evaluate_reply(self):
        """evaluate_reply delegates to engine after setup."""
        ctx = make_ctx(config={
            "interest_keywords": ["ai"],
            "engagement_threshold": 20.0,
        })
        cap = AnalyzerCapability(ctx)
        await cap.setup()

        result = cap.evaluate_reply("Great AI post!", "AI Topic", "Bot")
        assert isinstance(result.score, float)
