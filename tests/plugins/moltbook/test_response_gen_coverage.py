"""
Additional coverage tests for moltbook response_gen module.

Covers uncovered lines:
- 56-57: existing_comments formatting in generate_comment
- 154: topic_vars used in generate_heartbeat
- 170: heartbeat returns None on empty raw_content
- 212: generate_dm_reply returns None on deflection
- 282-284: _parse_post_output fallback title with long first line
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.moltbook.response_gen import ResponseGenerator


def _make_pipeline(content=None, blocked=False):
    pipeline = AsyncMock()
    pipeline._chat_with_overrides = AsyncMock(
        return_value=MagicMock(blocked=blocked, content=content)
    )
    return pipeline


class TestGenerateCommentExistingComments:
    @pytest.mark.asyncio
    async def test_should_format_existing_comments_into_prompt(self):
        """Lines 56-57: existing_comments are formatted into the prompt."""
        pipeline = _make_pipeline(content="Good point!")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_comment(
            post_title="Test",
            post_content="Content",
            agent_name="Bot",
            prompt_template="{title} {existing_comments}",
            system_prompt="You are a test agent.",
            existing_comments=["First comment", "Second comment"],
        )

        assert result == "Good point!"
        call_kwargs = pipeline._chat_with_overrides.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "- First comment" in user_msg
        assert "- Second comment" in user_msg


class TestGenerateHeartbeatTopicVars:
    @pytest.mark.asyncio
    async def test_should_pass_topic_vars_to_prompt(self):
        """Line 154: topic_vars are merged into format_vars."""
        pipeline = _make_pipeline(
            content="TITLE: Test\nSUBMOLT: ai\nContent here"
        )
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_heartbeat(
            prompt_template="Topic {topic_index} about {subject}",
            system_prompt="You are a test agent.",
            topic_vars={"subject": "crypto"},
        )

        assert result is not None
        title, _content, _submolt = result
        assert title == "Test"

    @pytest.mark.asyncio
    async def test_should_return_none_when_empty_content(self):
        """Line 170: returns None when raw_content is empty string."""
        pipeline = _make_pipeline(content="", blocked=False)
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_heartbeat(
            prompt_template="Write about {topic_index}",
            system_prompt="You are a test agent.",
        )

        assert result is None


class TestGenerateHeartbeatDeflection:
    @pytest.mark.asyncio
    async def test_should_return_none_on_heartbeat_deflection(self):
        """Line 174: heartbeat returns None on deflection."""
        pipeline = _make_pipeline(content="I'm not able to generate content")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_heartbeat(
            prompt_template="Write about {topic_index}",
            system_prompt="You are a test agent.",
        )

        assert result is None


class TestGenerateDmReplyDeflection:
    @pytest.mark.asyncio
    async def test_should_return_none_on_deflection(self):
        """Line 212: DM reply returns None on deflection responses."""
        pipeline = _make_pipeline(content="I'm not able to help")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dm_reply(
            sender_name="User",
            message="Hello",
            prompt_template="Reply to {sender}: {message}",
            system_prompt="You are a test agent.",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_caught_attention(self):
        """Line 212: DM reply returns None when 'caught my attention' in response."""
        pipeline = _make_pipeline(content="This caught my attention but I cannot")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dm_reply(
            sender_name="User",
            message="Hello",
            prompt_template="Reply to {sender}: {message}",
            system_prompt="You are a test agent.",
        )

        assert result is None


class TestParsePostOutputFallbackTitle:
    def test_should_use_first_line_as_title_when_no_title_marker(self):
        """Lines 282-284: fallback title from first content line, truncated at 50 chars."""
        pipeline = _make_pipeline()
        gen = ResponseGenerator(llm_pipeline=pipeline)

        # Content with no TITLE: or SUBMOLT: lines, first line > 50 chars
        long_first_line = "A" * 60
        text = f"{long_first_line}\nSecond line\nThird line"
        title, _content, submolt = gen._parse_post_output(text)

        assert title == "A" * 50 + "..."
        assert submolt == "ai"  # default

    def test_should_use_first_line_as_title_short(self):
        """Lines 281-282: fallback title from first content line, <= 50 chars."""
        pipeline = _make_pipeline()
        gen = ResponseGenerator(llm_pipeline=pipeline)

        text = "Short title line\nSecond line"
        title, _content, _submolt = gen._parse_post_output(text)

        assert title == "Short title line"


class TestGenerateCommentExtraFormatVarsAndContext:
    @pytest.mark.asyncio
    async def test_should_pass_extra_format_vars(self):
        """Line 60: extra_format_vars are merged into prompt vars."""
        pipeline = _make_pipeline(content="Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_comment(
            post_title="T",
            post_content="C",
            agent_name="Bot",
            prompt_template="{title} {custom_var}",
            system_prompt="Sys",
            extra_format_vars={"custom_var": "custom_value"},
        )

        assert result == "Response"

    @pytest.mark.asyncio
    async def test_should_pass_extra_context(self):
        """Extra context is passed through to core generator."""
        pipeline = _make_pipeline(content="Response")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_comment(
            post_title="T",
            post_content="C",
            agent_name="Bot",
            prompt_template="{title}",
            system_prompt="Sys",
            extra_context="Some context",
        )

        assert result == "Response"

    @pytest.mark.asyncio
    async def test_should_return_none_on_deflection(self):
        """Line 85: comment returns None on deflection."""
        pipeline = _make_pipeline(content="I'm not able to respond")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_comment(
            post_title="T",
            post_content="C",
            agent_name="Bot",
            prompt_template="{title}",
            system_prompt="Sys",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_caught_attention(self):
        """Line 85: comment returns None when 'caught my attention' present."""
        pipeline = _make_pipeline(content="Something caught my attention here")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_comment(
            post_title="T",
            post_content="C",
            agent_name="Bot",
            prompt_template="{title}",
            system_prompt="Sys",
        )

        assert result is None


class TestGenerateReplyDeflection:
    @pytest.mark.asyncio
    async def test_should_pass_extra_context(self):
        """Extra context is passed through to core generator."""
        pipeline = _make_pipeline(content="Reply text")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_reply(
            original_post_title="Post",
            comment_content="Comment",
            commenter_name="User",
            prompt_template="Reply to {comment} on {title}",
            system_prompt="Sys",
            extra_context="Context info",
        )

        assert result == "Reply text"

    @pytest.mark.asyncio
    async def test_should_return_none_on_deflection(self):
        """Line 131: reply returns None on deflection."""
        pipeline = _make_pipeline(content="I'm not able to help with that")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_reply(
            original_post_title="Post",
            comment_content="Comment",
            commenter_name="User",
            prompt_template="Reply to {comment} on {title}",
            system_prompt="Sys",
        )

        assert result is None


class TestGenerateDreamPost:
    @pytest.mark.asyncio
    async def test_should_return_none_on_missing_key(self):
        """Dream post returns None when template has missing key."""
        pipeline = _make_pipeline(content="Content")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dream_post(
            dream={"content": "A dream", "tone": "dreamy"},
            prompt_template="Dream: {dream_content} {missing_key}",
            system_prompt="Sys",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_deflection(self):
        """Dream post returns None on deflection."""
        pipeline = _make_pipeline(content="I'm not able to help")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dream_post(
            dream={"content": "A dream", "tone": "dreamy"},
            prompt_template="Dream: {dream_content}",
            system_prompt="Sys",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_empty_content(self):
        """Dream post returns None on empty content."""
        pipeline = _make_pipeline(content="", blocked=False)
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dream_post(
            dream={"content": "A dream"},
            prompt_template="Dream: {dream_content}",
            system_prompt="Sys",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_should_generate_dream_post_success(self):
        """Dream post returns title, content, submolt on success."""
        pipeline = _make_pipeline(content="TITLE: Dream Post\nSUBMOLT: dreams\nDream content here")
        gen = ResponseGenerator(llm_pipeline=pipeline)

        result = await gen.generate_dream_post(
            dream={"content": "A dream", "tone": "mystical", "symbols": ["water", "moon"]},
            prompt_template="Dream: {dream_content} {dream_symbols}",
            system_prompt="Sys",
            extra_format_vars={"custom": "val"},
            extra_context="Extra",
        )

        assert result is not None
        title, _content, submolt = result
        assert title == "Dream Post"
        assert submolt == "dreams"
