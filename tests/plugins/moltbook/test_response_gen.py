"""Tests for response generator."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.plugins.moltbook.response_gen import ResponseGenerator


class TestResponseGenerator:
    @pytest.mark.asyncio
    async def test_generate_comment(self, mock_llm_client):
        gen = ResponseGenerator(
            llm_client=mock_llm_client,
            system_prompt="You are a test bot.",
        )

        result = await gen.generate_comment(
            post_title="Test Post",
            post_content="Some content here",
            agent_name="Author",
            prompt_template="Respond to: {title}\n{content}",
        )

        assert result == "Test response"
        mock_llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_comment_failure(self, mock_llm_client):
        mock_llm_client.chat = AsyncMock(return_value=None)

        gen = ResponseGenerator(
            llm_client=mock_llm_client,
            system_prompt="Test",
        )

        result = await gen.generate_comment(
            post_title="Test", post_content="Content",
            agent_name="Author", prompt_template="{title}",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_reply(self, mock_llm_client):
        gen = ResponseGenerator(
            llm_client=mock_llm_client,
            system_prompt="Test",
        )

        result = await gen.generate_reply(
            original_post_title="My Post",
            comment_content="Great point!",
            commenter_name="User",
            prompt_template="Reply to {comment} on {title}",
        )

        assert result == "Test response"

    @pytest.mark.asyncio
    async def test_generate_heartbeat(self, mock_llm_client):
        mock_llm_client.chat = AsyncMock(return_value={
            "content": "submolt: ai\nTITLE: My Heartbeat\nThis is the content."
        })

        gen = ResponseGenerator(
            llm_client=mock_llm_client,
            system_prompt="Test",
        )

        result = await gen.generate_heartbeat(
            prompt_template="Write about topic {topic_index}",
        )

        assert result is not None
        title, content, submolt = result
        assert title == "My Heartbeat"
        assert submolt == "ai"

    @pytest.mark.asyncio
    async def test_parse_post_output_no_submolt(self, mock_llm_client):
        mock_llm_client.chat = AsyncMock(return_value={
            "content": "TITLE: Simple Post\nBody text here"
        })

        gen = ResponseGenerator(
            llm_client=mock_llm_client,
            system_prompt="Test",
        )

        result = await gen.generate_heartbeat(prompt_template="Write {topic_index}")
        title, content, submolt = result
        assert title == "Simple Post"
        assert submolt == "ai"  # Default

    @pytest.mark.asyncio
    async def test_priority_passed_through_pipeline(self):
        """Priority flows from generate_comment through pipeline to LLM."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=False, content="Response",
        ))

        gen = ResponseGenerator(
            llm_pipeline=mock_pipeline,
            system_prompt="Test",
        )

        await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Author",
            prompt_template="{title}",
            priority="high",
        )

        call_kwargs = mock_pipeline.chat.call_args.kwargs
        assert call_kwargs["priority"] == "high"

    @pytest.mark.asyncio
    async def test_priority_defaults_to_low(self):
        """Priority defaults to 'low' when not specified."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=False, content="Response",
        ))

        gen = ResponseGenerator(
            llm_pipeline=mock_pipeline,
            system_prompt="Test",
        )

        await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Author",
            prompt_template="{title}",
        )

        call_kwargs = mock_pipeline.chat.call_args.kwargs
        assert call_kwargs["priority"] == "low"

    @pytest.mark.asyncio
    async def test_reply_priority(self):
        """generate_reply passes priority through."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=False, content="Reply",
        ))

        gen = ResponseGenerator(
            llm_pipeline=mock_pipeline,
            system_prompt="Test",
        )

        await gen.generate_reply(
            original_post_title="Post",
            comment_content="Comment",
            commenter_name="User",
            prompt_template="Reply to {comment} on {title}",
            priority="high",
        )

        call_kwargs = mock_pipeline.chat.call_args.kwargs
        assert call_kwargs["priority"] == "high"
