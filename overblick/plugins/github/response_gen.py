"""
Response generator for the GitHub monitoring plugin.

Uses SafeLLMPipeline to generate identity-voiced responses
to GitHub issues and comments. Two modes:
1. Code question — includes code context from CodeContextBuilder
2. General issue — uses issue body and comments only
"""

import logging
from typing import Optional

from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.models import CodeContext, GitHubEvent
from overblick.plugins.github.prompts import code_question_prompt, issue_response_prompt

logger = logging.getLogger(__name__)

# Keywords suggesting a code-related question
_CODE_KEYWORDS = frozenset({
    "code", "function", "class", "method", "bug", "error", "traceback",
    "exception", "import", "module", "file", "line", "syntax", "return",
    "variable", "parameter", "argument", "type", "implementation",
    "stack", "debug", "fix", "patch", "diff", "commit", "PR", "pull request",
})


class ResponseGenerator:
    """
    Generate identity-voiced responses for GitHub events.

    Uses the SafeLLMPipeline with high complexity and priority
    to produce quality responses.
    """

    def __init__(
        self,
        llm_pipeline,
        code_context_builder: CodeContextBuilder,
        system_prompt: str = "",
    ):
        self._llm_pipeline = llm_pipeline
        self._code_context = code_context_builder
        self._system_prompt = system_prompt

    async def generate(
        self,
        event: GitHubEvent,
        existing_comments: Optional[list[dict]] = None,
        branch: str = "main",
    ) -> Optional[str]:
        """
        Generate a response for a GitHub event.

        Automatically determines whether to use code context based
        on the question content.

        Args:
            event: The GitHub event to respond to
            existing_comments: Previous comments on the issue
            branch: Default branch for code context

        Returns:
            Generated response text, or None if generation failed
        """
        if not self._llm_pipeline:
            logger.warning("GitHub: no LLM pipeline for response generation")
            return None

        # Format existing comments
        comments_text = self._format_comments(existing_comments)

        # Determine if code context is needed
        is_code_question = self._is_code_question(event)

        if is_code_question:
            return await self._generate_with_code(event, comments_text, branch)
        else:
            return await self._generate_general(event, comments_text)

    async def _generate_with_code(
        self,
        event: GitHubEvent,
        comments_text: str,
        branch: str,
    ) -> Optional[str]:
        """Generate response with code context."""
        question = event.body or event.issue_title

        context = await self._code_context.build_context(
            repo=event.repo,
            question=question,
            branch=branch,
        )

        code_text = CodeContextBuilder.format_context(context)
        safe_question = wrap_external_content(question, "github_question")
        safe_issue = wrap_external_content(event.issue_title, "github_issue")
        safe_comments = wrap_external_content(comments_text, "github_comments") if comments_text else ""

        messages = code_question_prompt(
            system_prompt=self._system_prompt,
            question=safe_question,
            code_context=code_text,
            existing_comments=safe_comments,
            issue_body=safe_issue,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_code_response",
                skip_preflight=True,
                complexity="high",
                priority="high",
            )
            if result and not result.blocked and result.content:
                return result.content.strip()
        except Exception as e:
            logger.error("GitHub: code response generation failed: %s", e, exc_info=True)

        return None

    async def _generate_general(
        self,
        event: GitHubEvent,
        comments_text: str,
    ) -> Optional[str]:
        """Generate response without code context."""
        safe_title = wrap_external_content(event.issue_title, "github_issue_title")
        safe_body = wrap_external_content(event.body[:3000], "github_issue_body")
        safe_comments = wrap_external_content(comments_text, "github_comments") if comments_text else ""

        messages = issue_response_prompt(
            system_prompt=self._system_prompt,
            issue_title=safe_title,
            issue_body=safe_body,
            existing_comments=safe_comments,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_issue_response",
                skip_preflight=True,
                complexity="high",
                priority="high",
            )
            if result and not result.blocked and result.content:
                return result.content.strip()
        except Exception as e:
            logger.error("GitHub: issue response generation failed: %s", e, exc_info=True)

        return None

    @staticmethod
    def _is_code_question(event: GitHubEvent) -> bool:
        """Determine if an event is asking a code-related question."""
        text = f"{event.issue_title} {event.body}".lower()
        matches = sum(1 for kw in _CODE_KEYWORDS if kw.lower() in text)
        return matches >= 2

    @staticmethod
    def _format_comments(comments: Optional[list[dict]]) -> str:
        """Format existing comments for prompt context."""
        if not comments:
            return ""

        parts = []
        for c in comments[-5:]:  # Last 5 comments only
            author = c.get("user", {}).get("login", "unknown")
            body = c.get("body", "")[:500]
            parts.append(f"@{author}: {body}")

        return "\n\n".join(parts)
