"""
Tests for the GitHub response generator.
"""

import pytest
from unittest.mock import AsyncMock

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.models import (
    CachedFile,
    CodeContext,
    EventType,
    GitHubEvent,
)
from overblick.plugins.github.response_gen import ResponseGenerator


@pytest.fixture
def mock_code_context_builder():
    """Mock CodeContextBuilder that returns predefined context."""
    builder = AsyncMock(spec=CodeContextBuilder)
    builder.build_context = AsyncMock(return_value=CodeContext(
        repo="test/repo",
        question="How does auth work?",
        files=[
            CachedFile(repo="test/repo", path="src/auth.py", sha="abc",
                       content="class AuthMiddleware:\n    def verify(self, token): ..."),
        ],
        total_size=100,
    ))
    return builder


@pytest.fixture
def response_gen(mock_llm_pipeline_github, mock_code_context_builder):
    """ResponseGenerator with mocked dependencies."""
    return ResponseGenerator(
        llm_pipeline=mock_llm_pipeline_github,
        code_context_builder=mock_code_context_builder,
        system_prompt="You are a helpful code assistant.",
    )


class TestResponseGenerator:
    """Test response generation logic."""

    def test_is_code_question_positive(self):
        """Code-related keywords trigger code context."""
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Bug in authentication module",
            body="The function returns an error when called with invalid token",
            author="user",
        )
        assert ResponseGenerator._is_code_question(event) is True

    def test_is_code_question_negative(self):
        """Non-code content does not trigger code context."""
        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Feature request: dark mode",
            body="It would be nice to have a dark theme option.",
            author="user",
        )
        assert ResponseGenerator._is_code_question(event) is False

    def test_format_comments_empty(self):
        """Empty comments return empty string."""
        assert ResponseGenerator._format_comments(None) == ""
        assert ResponseGenerator._format_comments([]) == ""

    def test_format_comments_limits_to_five(self):
        """Only last 5 comments are included."""
        comments = [
            {"user": {"login": f"user{i}"}, "body": f"Comment {i}"}
            for i in range(10)
        ]
        result = ResponseGenerator._format_comments(comments)
        assert "@user5:" in result
        assert "@user9:" in result
        # First 5 should not be present
        assert "@user0:" not in result

    def test_format_comments_truncates_body(self):
        """Long comment bodies are truncated."""
        comments = [
            {"user": {"login": "user"}, "body": "x" * 1000},
        ]
        result = ResponseGenerator._format_comments(comments)
        assert len(result) < 600

    @pytest.mark.asyncio
    async def test_generate_code_question(
        self, response_gen, mock_llm_pipeline_github, mock_code_context_builder,
    ):
        """Code questions trigger code context building."""
        mock_llm_pipeline_github.chat = AsyncMock(return_value=PipelineResult(
            content="The auth middleware verifies tokens using JWT."
        ))

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="How does the auth module work?",
            body="Can you explain the implementation of the authentication function?",
            author="user",
        )

        result = await response_gen.generate(event)

        assert result is not None
        assert "auth" in result.lower()
        mock_code_context_builder.build_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_general_issue(
        self, response_gen, mock_llm_pipeline_github, mock_code_context_builder,
    ):
        """General issues skip code context."""
        mock_llm_pipeline_github.chat = AsyncMock(return_value=PipelineResult(
            content="Great idea! Dark mode would improve readability."
        ))

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Feature request: dark mode",
            body="It would be nice to have a dark theme option.",
            author="user",
        )

        result = await response_gen.generate(event)

        assert result is not None
        assert "dark mode" in result.lower()
        mock_code_context_builder.build_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_returns_none_on_failure(self, response_gen, mock_llm_pipeline_github):
        """Returns None when LLM fails."""
        mock_llm_pipeline_github.chat = AsyncMock(return_value=PipelineResult(
            content=None, blocked=True, block_reason="safety",
        ))

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="General question",
            body="What's the roadmap?",
            author="user",
        )

        result = await response_gen.generate(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_no_pipeline(self, mock_code_context_builder):
        """Returns None when no LLM pipeline is available."""
        gen = ResponseGenerator(
            llm_pipeline=None,
            code_context_builder=mock_code_context_builder,
        )

        event = GitHubEvent(
            event_id="test/1",
            event_type=EventType.ISSUE_OPENED,
            repo="test/repo",
            issue_number=1,
            issue_title="Question",
            body="How?",
            author="user",
        )

        result = await gen.generate(event)
        assert result is None
