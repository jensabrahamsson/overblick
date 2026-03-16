"""Additional tests for ComposerCapability to reach 100% line coverage.

Covers uncovered lines: 75-76, 82, 103, 135, 154, 158, 180-182
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from overblick.capabilities.engagement.composer import ComposerCapability
from overblick.core.capability import CapabilityContext
from overblick.core.llm.pipeline import PipelineResult


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
    pipeline._chat_with_overrides = AsyncMock(
        return_value=PipelineResult(
            content=response_text,
            blocked=False,
            block_stage=None,
            block_reason=None,
            deflection=None,
            audit_id="test-audit-id",
        )
    )
    return pipeline


class TestComposerCoverageGaps:
    @pytest.mark.asyncio
    async def test_compose_comment_with_existing_comments(self):
        """Cover lines 74-76: existing_comments branch."""
        pipeline = make_mock_pipeline("Thoughtful comment.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are test."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="Comment on: {title}\n{content}\n{existing_comments}",
            existing_comments=["Great post!", "I agree!", "Very interesting"],
        )
        assert result == "Thoughtful comment."

    @pytest.mark.asyncio
    async def test_compose_comment_with_extra_context(self):
        """Cover line 82: extra_context branch."""
        pipeline = make_mock_pipeline("Context-aware comment.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are test."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="Comment on: {title}\n{content}",
            extra_context="Some extra context here",
        )
        assert result == "Context-aware comment."

    @pytest.mark.asyncio
    async def test_compose_reply_not_initialized(self):
        """Cover line 103: compose_reply returns None when not initialized."""
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        result = await cap.compose_reply("Title", "Comment", "Commenter", "template")
        assert result is None

    @pytest.mark.asyncio
    async def test_compose_heartbeat_not_initialized(self):
        """Cover line 135: compose_heartbeat returns None when not initialized."""
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        result = await cap.compose_heartbeat("template {topic_index}")
        assert result is None

    @pytest.mark.asyncio
    async def test_compose_heartbeat_returns_none_when_no_content(self):
        """Cover line 154: raw_content is None."""
        pipeline = AsyncMock()
        pipeline._chat_with_overrides = AsyncMock(
            return_value=PipelineResult(content=None)
        )
        # Need a real generator

        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={"system_prompt": "You are test."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        # Mock the generator to return None
        cap._generator = AsyncMock()
        cap._generator.generate = AsyncMock(return_value=None)

        result = await cap.compose_heartbeat("template {topic_index}")
        assert result is None

    @pytest.mark.asyncio
    async def test_compose_heartbeat_deflection_response(self):
        """Cover line 158: deflection detected in raw_content."""
        ctx = make_ctx(
            llm_pipeline=make_mock_pipeline(),
            config={"system_prompt": "You are test."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        cap._generator = AsyncMock()
        cap._generator.generate = AsyncMock(
            return_value="I'm not able to generate that content."
        )

        result = await cap.compose_heartbeat("template {topic_index}")
        assert result is not None
        title, _content, submolt = result
        assert title == "Untitled Post"
        assert submolt == "ai"

    @pytest.mark.asyncio
    async def test_compose_heartbeat_caught_my_attention_deflection(self):
        """Cover the second deflection check in line 156."""
        ctx = make_ctx(
            llm_pipeline=make_mock_pipeline(),
            config={"system_prompt": "You are test."},
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        cap._generator = AsyncMock()
        cap._generator.generate = AsyncMock(
            return_value="Something caught my attention recently."
        )

        result = await cap.compose_heartbeat("template {topic_index}")
        assert result is not None
        title, _content, submolt = result
        assert title == "Untitled Post"
        assert submolt == "ai"

    def test_parse_post_output_title_fallback_short(self):
        """Cover lines 180-181: title fallback from first content line <= 50 chars."""
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        result = cap._parse_post_output("Short first line\nSecond line")
        title, _content, _submolt = result
        assert title == "Short first line"

    def test_parse_post_output_title_fallback_long(self):
        """Cover lines 180-182: title fallback from first content line > 50 chars."""
        ctx = make_ctx()
        cap = ComposerCapability(ctx)
        long_line = "A" * 60
        result = cap._parse_post_output(f"{long_line}\nSecond line")
        title, _content, _submolt = result
        assert title == "A" * 50 + "..."

    @pytest.mark.asyncio
    async def test_compose_comment_uses_default_config(self):
        """Cover defaults for system_prompt, temperature, max_tokens."""
        pipeline = make_mock_pipeline("Response.")
        ctx = make_ctx(
            llm_pipeline=pipeline,
            config={},  # No config — use defaults
        )
        cap = ComposerCapability(ctx)
        await cap.setup()

        result = await cap.compose_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title} {content}",
        )
        assert result == "Response."
