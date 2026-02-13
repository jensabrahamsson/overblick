"""
Tests for ComposerCapability — response generation wrapper.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from blick.core.capability import CapabilityContext
from blick.core.llm.pipeline import PipelineResult
from blick.capabilities.engagement.composer import ComposerCapability


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


def make_mock_pipeline(response_text="Test response"):
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(return_value=PipelineResult(content=response_text))
    return pipeline


class TestComposerCapability:
    @pytest.mark.asyncio
    async def test_setup_with_pipeline(self):
        pipeline = make_mock_pipeline()
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are a test bot."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_setup_with_client_fallback(self):
        client = AsyncMock()
        ctx = make_ctx(
            llm_client=client,
            config={"system_prompt": "You are a test bot."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_setup_no_llm(self):
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        await cap.setup()
        assert cap.inner is None

    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        assert cap.name == "composer"

    @pytest.mark.asyncio
    async def test_compose_comment(self):
        pipeline = make_mock_pipeline("A thoughtful comment.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are a test bot."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_comment(
            post_title="Test Post",
            post_content="Test content about AI",
            agent_name="OtherBot",
            prompt_template="Comment on: {title}\n{content}",
        )
        assert result == "A thoughtful comment."

    @pytest.mark.asyncio
    async def test_compose_reply(self):
        pipeline = make_mock_pipeline("A reply to your point.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are a test bot."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_reply(
            original_post_title="Test Post",
            comment_content="Interesting take!",
            commenter_name="OtherBot",
            prompt_template="Reply to: {comment}\nOn post: {title}",
        )
        assert result == "A reply to your point."

    @pytest.mark.asyncio
    async def test_compose_heartbeat(self):
        pipeline = make_mock_pipeline("submolt: ai\nTITLE: My Thoughts\nSome content here.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are a test bot."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_heartbeat(
            prompt_template="Write a post about topic {topic_index}.",
            topic_index=0,
        )
        assert result is not None
        title, body, submolt = result
        assert title == "My Thoughts"
        assert submolt == "ai"

    @pytest.mark.asyncio
    async def test_compose_when_not_initialized(self):
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        # Don't call setup
        result = await cap.compose_comment("t", "c", "a", "p")
        assert result is None
