"""
Additional coverage tests for github response_gen module.

Covers uncovered lines:
- 146-149: _generate_with_code exception handling
- 179-180: _generate_general exception handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.models import (
    CodeContext,
    EventType,
    GitHubEvent,
)
from overblick.plugins.github.response_gen import ResponseGenerator


@pytest.fixture
def mock_code_builder():
    builder = AsyncMock(spec=CodeContextBuilder)
    builder.build_context = AsyncMock(
        return_value=CodeContext(
            repo="test/repo",
            question="test",
            files=[],
            total_size=0,
        )
    )
    return builder


class TestGenerateWithCodeException:
    @pytest.mark.asyncio
    async def test_should_return_none_on_llm_exception(self, mock_code_builder):
        """Lines 146-149: exception in code response returns None."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            code_context_builder=mock_code_builder,
            system_prompt="Sys",
        )

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Bug in function code",
            body="The function returns an error in the module implementation",
            author="user",
        )

        result = await gen.generate(event)
        assert result is None


class TestGenerateGeneralException:
    @pytest.mark.asyncio
    async def test_should_return_none_on_llm_exception(self, mock_code_builder):
        """Lines 179-180: exception in general response returns None."""
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        gen = ResponseGenerator(
            llm_pipeline=pipeline,
            code_context_builder=mock_code_builder,
            system_prompt="Sys",
        )

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Feature request",
            body="Please add dark mode",
            author="user",
        )

        result = await gen.generate(event)
        assert result is None
