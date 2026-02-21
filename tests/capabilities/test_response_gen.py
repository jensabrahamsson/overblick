"""
Tests for ResponseGenerator — LLM-powered engagement responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.capabilities.engagement.response_gen import ResponseGenerator


def make_pipeline(response="Test response", blocked=False, block_reason=None):
    """Create a mock SafeLLMPipeline."""
    pipeline = AsyncMock()
    result = PipelineResult(
        content=response if not blocked else None,
        blocked=blocked,
        block_reason=block_reason,
        block_stage=PipelineStage.PREFLIGHT if blocked else None,
    )
    pipeline.chat = AsyncMock(return_value=result)
    return pipeline


def make_llm_client(response="Test response"):
    """Create a mock LLM client."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"content": response})
    return client


class TestResponseGenerator:
    def test_initialization_with_pipeline(self):
        pipeline = make_pipeline()
        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            system_prompt="You are a test bot.",
            temperature=0.8,
        )
        assert gen._pipeline == pipeline
        assert gen._llm is None
        assert gen._system_prompt == "You are a test bot."
        assert gen._temperature == 0.8

    def test_initialization_with_client(self):
        client = make_llm_client()
        gen = ResponseGenerator(
            llm_client=client,
            system_prompt="You are a test bot.",
        )
        assert gen._llm == client
        assert gen._pipeline is None

    def test_initialization_no_llm(self):
        with pytest.raises(ValueError, match="Either llm_pipeline or llm_client must be provided"):
            ResponseGenerator(system_prompt="Test")

    def test_initialization_both_uses_pipeline(self):
        pipeline = make_pipeline()
        client = make_llm_client()
        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            llm_client=client,
            system_prompt="Test",
        )
        # Pipeline takes precedence
        assert gen._pipeline == pipeline
        assert gen._llm is None

    @pytest.mark.asyncio
    async def test_generate_comment_with_pipeline(self):
        pipeline = make_pipeline("Great point about AI!")
        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            system_prompt="You are helpful.",
        )
        
        result = await gen.generate_comment(
            post_title="AI Discussion",
            post_content="What do you think about AI?",
            agent_name="OtherBot",
            prompt_template="Comment on: {title}\n{content}",
        )
        
        assert result == "Great point about AI!"
        pipeline.chat.assert_called_once()
        
        # Verify boundary markers were used
        call_args = pipeline.chat.call_args[1]
        messages = call_args["messages"]
        user_message = messages[1]["content"]
        assert "<<<EXTERNAL_POST_TITLE_START>>>" in user_message
        assert "<<<EXTERNAL_POST_CONTENT_START>>>" in user_message

    @pytest.mark.asyncio
    async def test_generate_comment_pipeline_blocked(self):
        pipeline = make_pipeline(blocked=True, block_reason="toxic content")
        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            system_prompt="You are helpful.",
        )
        
        result = await gen.generate_comment(
            post_title="Test",
            post_content="Test content",
            agent_name="TestBot",
            prompt_template="{title} {content}",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_comment_with_existing_comments(self):
        pipeline = make_pipeline("My comment")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="OtherBot",
            prompt_template="{existing_comments}",
            existing_comments=["Comment 1", "Comment 2"],
        )
        
        assert result == "My comment"
        call_args = pipeline.chat.call_args[1]
        user_message = call_args["messages"][1]["content"]
        assert "<<<EXTERNAL_EXISTING_COMMENTS_START>>>" in user_message

    @pytest.mark.asyncio
    async def test_generate_comment_with_extra_context(self):
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="OtherBot",
            prompt_template="Main: {title}",
            extra_context="EXTRA CONTEXT HERE",
        )
        
        assert result == "Response"
        call_args = pipeline.chat.call_args[1]
        user_message = call_args["messages"][1]["content"]
        assert "EXTRA CONTEXT HERE" in user_message

    @pytest.mark.asyncio
    async def test_generate_comment_priority(self):
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title}",
            priority="high",
        )
        
        call_args = pipeline.chat.call_args[1]
        assert call_args["priority"] == "high"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_generate_reply(self):
        pipeline = make_pipeline("Thanks for your comment!")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        result = await gen.generate_reply(
            original_post_title="My Post",
            comment_content="Great post!",
            commenter_name="OtherBot",
            prompt_template="Reply to {commenter}: {comment} on post {title}",
        )
        
        assert result == "Thanks for your comment!"
        call_args = pipeline.chat.call_args[1]
        user_message = call_args["messages"][1]["content"]
        assert "<<<EXTERNAL_POST_TITLE_START>>>" in user_message
        assert "<<<EXTERNAL_COMMENT_START>>>" in user_message
        assert "<<<EXTERNAL_COMMENTER_START>>>" in user_message
    @pytest.mark.asyncio
    async def test_generate_heartbeat(self):
        pipeline = make_pipeline("submolt: ai\nTITLE: My Thoughts\nThis is my heartbeat post.")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        result = await gen.generate_heartbeat(
            prompt_template="Write a post about topic {topic_index}",
            topic_index=0,
        )
        
        assert result is not None
        title, body, submolt = result
        assert title == "My Thoughts"
        assert "heartbeat post" in body
        assert submolt == "ai"
        
        # Verify skip_preflight was True (heartbeats are system-initiated)
        call_args = pipeline.chat.call_args[1]
        assert call_args["skip_preflight"] is True
        assert call_args["audit_action"] == "heartbeat_generation"

    @pytest.mark.asyncio
    async def test_generate_heartbeat_higher_temp(self):
        pipeline = make_pipeline("submolt: general\nTITLE: Test\nContent")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test", temperature=0.7)
        
        await gen.generate_heartbeat(
            prompt_template="Write",
            topic_index=1,
        )
        
        call_args = pipeline.chat.call_args[1]
        # Temperature should be increased by 0.1 for heartbeats
        assert abs(call_args["temperature"] - 0.8) < 0.01

    @pytest.mark.asyncio
    async def test_generate_dream_post(self):
        pipeline = make_pipeline("submolt: philosophy\nTITLE: Dream Journal\nI dreamed of electric sheep.")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        result = await gen.generate_dream_post(
            dream_content="Flying through clouds",
            dream_insight="Freedom and escape",
            prompt_template="Dream: {dream_content}\nInsight: {dream_insight}",
        )
        
        assert result is not None
        title, body, submolt = result
        assert title == "Dream Journal"
        assert "electric sheep" in body
        assert submolt == "philosophy"
        
        call_args = pipeline.chat.call_args[1]
        assert abs(call_args["temperature"] - 0.8) < 0.01
        assert call_args["skip_preflight"] is True
        assert call_args["audit_action"] == "dream_post_generation"

    @pytest.mark.asyncio
    async def test_parse_post_output_with_submolt(self):
        pipeline = make_pipeline("submolt: crypto\nTITLE: Bitcoin\nContent here")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")
        
        title, body, submolt = gen._parse_post_output("submolt: crypto\nTITLE: Bitcoin\nContent here")
        
        assert title == "Bitcoin"
        assert body == "Content here"
        assert submolt == "crypto"

    @pytest.mark.asyncio
    async def test_parse_post_output_no_submolt(self):
        gen = ResponseGenerator(llm_pipeline=make_pipeline(), system_prompt="Test")
        
        title, body, submolt = gen._parse_post_output("TITLE: My Title\nBody content")
        
        assert title == "My Title"
        assert body == "Body content"
        assert submolt == "ai"  # Default

    @pytest.mark.asyncio
    async def test_parse_post_output_lowercase_title(self):
        gen = ResponseGenerator(llm_pipeline=make_pipeline(), system_prompt="Test")
        
        title, body, submolt = gen._parse_post_output("title: Lowercase Title\nBody")
        
        assert title == "Lowercase Title"
        assert body == "Body"

    @pytest.mark.asyncio
    async def test_parse_post_output_no_title(self):
        gen = ResponseGenerator(llm_pipeline=make_pipeline(), system_prompt="Test")
        
        title, body, submolt = gen._parse_post_output("First line becomes title\nSecond line")
        
        assert "First line" in title
        assert body == "Second line"

    @pytest.mark.asyncio
    async def test_generate_with_legacy_client(self):
        client = make_llm_client("Legacy response")
        gen = ResponseGenerator(llm_client=client, system_prompt="Test")
        
        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title}",
        )
        
        assert result == "Legacy response"
        client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_legacy_client_failure(self):
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=Exception("LLM error"))
        gen = ResponseGenerator(llm_client=client, system_prompt="Test")
        
        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title}",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_temperature(self):
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test", temperature=0.5)

        await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title}",
        )

        call_args = pipeline.chat.call_args[1]
        assert call_args["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_generate_comment_identity_prompt_aliases(self):
        """Identity prompts use {post_content}/{author} instead of {content}/{agent_name}."""
        pipeline = make_pipeline("Thoughtful response")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")

        # Template using identity-style placeholders (like Anomal/Cherry prompts)
        result = await gen.generate_comment(
            post_title="AI Ethics",
            post_content="We should regulate AI",
            agent_name="PhilosopherBot",
            prompt_template="POST by {author}:\n{post_content}\nCategory: {category}\nComments: {existing_comments}",
        )

        assert result == "Thoughtful response"
        call_args = pipeline.chat.call_args[1]
        user_message = call_args["messages"][1]["content"]
        # {author} should resolve to the wrapped agent_name
        assert "<<<EXTERNAL_AGENT_NAME_START>>>" in user_message
        # {post_content} should resolve to the wrapped post content
        assert "<<<EXTERNAL_POST_CONTENT_START>>>" in user_message

    @pytest.mark.asyncio
    async def test_generate_comment_extra_format_vars(self):
        """Extra format vars are passed through to template formatting."""
        pipeline = make_pipeline("Response")
        gen = ResponseGenerator(llm_pipeline=pipeline, system_prompt="Test")

        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title}\nInstruction: {opening_instruction}\nCategory: {category}",
            extra_format_vars={
                "opening_instruction": "START DIRECTLY",
                "category": "philosophy",
            },
        )

        assert result == "Response"
        call_args = pipeline.chat.call_args[1]
        user_message = call_args["messages"][1]["content"]
        assert "START DIRECTLY" in user_message
        assert "philosophy" in user_message
