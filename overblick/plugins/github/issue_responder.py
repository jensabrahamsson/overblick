"""
IssueResponder — classify issues and generate identity-voiced responses.

Reuses the existing ResponseGenerator and CodeContextBuilder for
actual response generation. Adds issue classification and triage
as an agentic layer on top.
"""

import hashlib
import json
import logging
from typing import Optional

from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.plugins.github.client import GitHubAPIClient, GitHubAPIError
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import (
    ActionOutcome,
    CommentRecord,
    EventType,
    GitHubEvent,
    IssueSnapshot,
    PlannedAction,
)
from overblick.plugins.github.prompts import issue_classification_prompt
from overblick.plugins.github.response_gen import ResponseGenerator

logger = logging.getLogger(__name__)


class IssueResponder:
    """
    Responds to GitHub issues using the identity-voiced pipeline.

    Flow:
    1. Classify the issue (question, bug, feature request, etc.)
    2. Determine if code context is needed
    3. Generate a response via ResponseGenerator
    4. Post the response as a comment
    """

    def __init__(
        self,
        client: GitHubAPIClient,
        db: GitHubDB,
        response_gen: ResponseGenerator,
        llm_pipeline=None,
        dry_run: bool = True,
        respond_to_labels: Optional[list[str]] = None,
        max_response_age_hours: int = 168,
    ):
        self._client = client
        self._db = db
        self._response_gen = response_gen
        self._llm_pipeline = llm_pipeline
        self._dry_run = dry_run
        self._respond_to_labels = [
            l.lower() for l in (respond_to_labels or ["question", "help wanted", "bug"])
        ]
        self._max_response_age_hours = max_response_age_hours

    async def handle_respond(
        self,
        action: PlannedAction,
        issue: IssueSnapshot,
        default_branch: str = "main",
    ) -> ActionOutcome:
        """
        Respond to a GitHub issue.

        Args:
            action: The planned action
            issue: Issue snapshot from observation
            default_branch: Default branch for code context

        Returns:
            ActionOutcome with success/failure details
        """
        repo = action.repo

        # Skip if already responded
        if issue.has_our_response:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Already responded to issue #{issue.number}",
            )

        # Skip if too old
        if issue.age_hours > self._max_response_age_hours:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Issue #{issue.number} is too old ({issue.age_hours:.0f}h)",
            )

        # Build a GitHubEvent for the ResponseGenerator
        event = GitHubEvent(
            event_id=f"{repo}/issues/{issue.number}",
            event_type=EventType.ISSUE_OPENED,
            repo=repo,
            issue_number=issue.number,
            issue_title=issue.title,
            body=issue.body,
            author=issue.author,
            labels=issue.labels,
            created_at=issue.created_at,
        )

        # Fetch existing comments for context
        existing_comments = []
        try:
            existing_comments = await self._client.list_issue_comments(
                repo, issue.number,
            )
        except GitHubAPIError as e:
            logger.debug("Failed to fetch comments for %s#%d: %s", repo, issue.number, e)

        # Generate response
        response_text = await self._response_gen.generate(
            event=event,
            existing_comments=existing_comments,
            branch=default_branch,
        )

        if not response_text:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Response generation failed for issue #{issue.number}",
            )

        # Dry run
        if self._dry_run:
            logger.info(
                "DRY RUN: would respond to %s#%d: %s (response: %d chars)",
                repo, issue.number, issue.title, len(response_text),
            )
            return ActionOutcome(
                action=action,
                success=True,
                result=f"DRY RUN: would respond to issue #{issue.number} ({len(response_text)} chars)",
            )

        # Post comment
        try:
            result = await self._client.create_comment(
                repo, issue.number, response_text,
            )
            comment_id = result.get("id", 0)

            # Record in DB
            content_hash = hashlib.sha256(response_text.encode()).hexdigest()[:16]
            await self._db.record_comment(CommentRecord(
                github_comment_id=comment_id,
                repo=repo,
                issue_number=issue.number,
                content_hash=content_hash,
            ))

            logger.info("Responded to %s#%d: %s", repo, issue.number, issue.title)

            return ActionOutcome(
                action=action,
                success=True,
                result=f"Responded to issue #{issue.number}: {issue.title}",
            )

        except GitHubAPIError as e:
            logger.error("Failed to post comment on %s#%d: %s", repo, issue.number, e)
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Failed to post comment: {e}",
            )

    def should_respond(self, issue: IssueSnapshot) -> bool:
        """Check if an issue matches our response criteria."""
        if issue.has_our_response:
            return False
        if issue.age_hours > self._max_response_age_hours:
            return False

        # Check labels
        issue_labels = [l.lower() for l in issue.labels]
        for label in self._respond_to_labels:
            if label in issue_labels:
                return True

        return False
