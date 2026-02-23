"""
GitHub action handlers — domain-specific ActionHandler implementations.

Each handler wraps existing GitHub-specific logic (DependabotHandler,
IssueResponder, etc.) to implement the core ActionHandler protocol.

Provides build_github_handlers() factory to create the handler dict
for the core ActionExecutor.
"""

import logging
from typing import Any, Optional

from overblick.core.agentic.models import ActionOutcome, PlannedAction
from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.dependabot_handler import DependabotHandler
from overblick.plugins.github.issue_responder import IssueResponder
from overblick.plugins.github.models import (
    ActionType,
    IssueSnapshot,
    PRSnapshot,
    RepoObservation,
    VersionBumpType,
)

logger = logging.getLogger(__name__)


def _find_pr(
    action: PlannedAction,
    observation: Any,
) -> Optional[PRSnapshot]:
    """Find a PR in observations by number."""
    if not isinstance(observation, dict):
        return None
    obs = observation.get(action.repo)
    if not obs:
        return None
    for pr in obs.open_prs:
        if pr.number == action.target_number:
            return pr
    return None


def _find_issue(
    action: PlannedAction,
    observation: Any,
) -> Optional[IssueSnapshot]:
    """Find an issue in observations by number."""
    if not isinstance(observation, dict):
        return None
    obs = observation.get(action.repo)
    if not obs:
        return None
    for issue in obs.open_issues:
        if issue.number == action.target_number:
            return issue
    return None


class MergePRHandler:
    """Handle merge_pr actions (delegates to DependabotHandler)."""

    def __init__(self, dependabot: DependabotHandler):
        self._dependabot = dependabot

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        pr = _find_pr(action, observation)
        if not pr:
            return ActionOutcome(
                action=action, success=False,
                error=f"PR #{action.target_number} not found in observations",
            )

        if not pr.is_dependabot:
            return ActionOutcome(
                action=action, success=False,
                error="Only Dependabot PRs can be auto-merged",
            )

        if pr.version_bump == VersionBumpType.MAJOR:
            return await self._dependabot.review_major_bump(action, pr)

        return await self._dependabot.handle_merge(action, pr)


class ApprovePRHandler:
    """Handle approve_pr actions."""

    def __init__(self, client: GitHubAPIClient, dry_run: bool = True):
        self._client = client
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        pr = _find_pr(action, observation)
        if not pr:
            return ActionOutcome(
                action=action, success=False,
                error=f"PR #{action.target_number} not found in observations",
            )

        if self._dry_run:
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would approve PR #{pr.number}",
            )

        try:
            await self._client.create_pull_review(
                action.repo, pr.number,
                event="APPROVE",
                body=action.reasoning or "Approved by Överblick agent.",
            )
            return ActionOutcome(
                action=action, success=True,
                result=f"Approved PR #{pr.number}: {pr.title}",
            )
        except Exception as e:
            return ActionOutcome(
                action=action, success=False,
                error=f"Failed to approve PR: {e}",
            )


class ReviewPRHandler:
    """Handle review_pr actions (comment review)."""

    def __init__(self, client: GitHubAPIClient, dry_run: bool = True):
        self._client = client
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        pr = _find_pr(action, observation)
        if not pr:
            return ActionOutcome(
                action=action, success=False,
                error=f"PR #{action.target_number} not found in observations",
            )

        if self._dry_run:
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would review PR #{pr.number}",
            )

        try:
            body = action.reasoning or "Reviewed by Överblick agent."
            await self._client.create_pull_review(
                action.repo, pr.number,
                event="COMMENT",
                body=body,
            )
            return ActionOutcome(
                action=action, success=True,
                result=f"Reviewed PR #{pr.number}: {pr.title}",
            )
        except Exception as e:
            return ActionOutcome(
                action=action, success=False,
                error=f"Failed to review PR: {e}",
            )


class RespondIssueHandler:
    """Handle respond_issue actions (delegates to IssueResponder)."""

    def __init__(self, issue_responder: IssueResponder, default_branch: str = "main"):
        self._issue_responder = issue_responder
        self._default_branch = default_branch

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        issue = _find_issue(action, observation)
        if not issue:
            return ActionOutcome(
                action=action, success=False,
                error=f"Issue #{action.target_number} not found in observations",
            )

        return await self._issue_responder.handle_respond(
            action, issue, default_branch=self._default_branch,
        )


class NotifyOwnerHandler:
    """Handle notify_owner actions."""

    def __init__(self, notify_fn=None, dry_run: bool = True):
        self._notify_fn = notify_fn
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        message = (
            f"*GitHub Agent: {action.repo}*\n"
            f"{action.target}\n\n"
            f"_{action.reasoning}_"
        )

        if self._dry_run:
            logger.info("DRY RUN: would notify owner: %s", message[:200])
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would notify owner about {action.target}",
            )

        if self._notify_fn:
            try:
                await self._notify_fn(message)
                return ActionOutcome(
                    action=action, success=True,
                    result=f"Notified owner about {action.target}",
                )
            except Exception as e:
                return ActionOutcome(
                    action=action, success=False,
                    error=f"Notification failed: {e}",
                )

        return ActionOutcome(
            action=action, success=False,
            error="No notification function available",
        )


class CommentPRHandler:
    """Handle comment_pr actions."""

    def __init__(self, client: GitHubAPIClient, dry_run: bool = True):
        self._client = client
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        if self._dry_run:
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would comment on PR #{action.target_number}",
            )

        body = action.params.get("body", action.reasoning or "")
        if not body:
            return ActionOutcome(
                action=action, success=False,
                error="No comment body provided",
            )

        try:
            await self._client.create_comment(
                action.repo, action.target_number, body,
            )
            return ActionOutcome(
                action=action, success=True,
                result=f"Commented on PR #{action.target_number}",
            )
        except Exception as e:
            return ActionOutcome(
                action=action, success=False,
                error=f"Failed to comment: {e}",
            )


class RefreshContextHandler:
    """Handle refresh_context actions (no-op — handled by observation phase)."""

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        return ActionOutcome(
            action=action, success=True,
            result="Context refresh noted (handled by observation phase)",
        )


class SkipHandler:
    """Handle skip actions."""

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        return ActionOutcome(
            action=action, success=True,
            result=f"Skipped: {action.reasoning}",
        )


def build_github_handlers(
    client: GitHubAPIClient,
    dependabot: DependabotHandler,
    issue_responder: IssueResponder,
    notify_fn=None,
    dry_run: bool = True,
    default_branch: str = "main",
) -> dict[str, Any]:
    """
    Build the complete handler dict for the GitHub agent.

    Returns a dict mapping ActionType string values to handler instances.
    """
    return {
        ActionType.MERGE_PR.value: MergePRHandler(dependabot),
        ActionType.APPROVE_PR.value: ApprovePRHandler(client, dry_run),
        ActionType.REVIEW_PR.value: ReviewPRHandler(client, dry_run),
        ActionType.RESPOND_ISSUE.value: RespondIssueHandler(issue_responder, default_branch),
        ActionType.NOTIFY_OWNER.value: NotifyOwnerHandler(notify_fn, dry_run),
        ActionType.COMMENT_PR.value: CommentPRHandler(client, dry_run),
        ActionType.REFRESH_CONTEXT.value: RefreshContextHandler(),
        ActionType.SKIP.value: SkipHandler(),
    }
