"""
ActionExecutor — dispatches planned actions to specialized handlers.

Validates each action before execution, respects max_actions_per_tick
and dry_run settings, and produces ActionOutcome records.
"""

import logging
import time
from typing import Optional

from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.dependabot_handler import DependabotHandler
from overblick.plugins.github.issue_responder import IssueResponder
from overblick.plugins.github.models import (
    ActionOutcome,
    ActionPlan,
    ActionType,
    IssueSnapshot,
    PlannedAction,
    PRSnapshot,
    RepoObservation,
    VersionBumpType,
)

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes planned actions by dispatching to specialized handlers.

    Safety guards:
    - Validates actions exist (PR/issue must be in observation)
    - Respects max_actions_per_tick
    - Respects dry_run mode
    - Records all outcomes
    """

    def __init__(
        self,
        client: GitHubAPIClient,
        db: GitHubDB,
        dependabot_handler: DependabotHandler,
        issue_responder: IssueResponder,
        notify_fn=None,
        max_actions_per_tick: int = 5,
        dry_run: bool = True,
        default_branch: str = "main",
    ):
        self._client = client
        self._db = db
        self._dependabot = dependabot_handler
        self._issue_responder = issue_responder
        self._notify_fn = notify_fn
        self._max_actions = max_actions_per_tick
        self._dry_run = dry_run
        self._default_branch = default_branch

    async def execute(
        self,
        plan: ActionPlan,
        observations: dict[str, RepoObservation],
    ) -> list[ActionOutcome]:
        """
        Execute a plan against the observed world state.

        Args:
            plan: The action plan from the planner
            observations: Map of repo -> RepoObservation

        Returns:
            List of action outcomes
        """
        outcomes: list[ActionOutcome] = []

        for i, action in enumerate(plan.actions):
            if i >= self._max_actions:
                logger.info(
                    "GitHub executor: max actions per tick reached (%d)",
                    self._max_actions,
                )
                break

            start_time = time.monotonic()
            outcome = await self._execute_action(action, observations)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            outcome.duration_ms = elapsed_ms

            outcomes.append(outcome)

            if outcome.success:
                logger.info(
                    "GitHub executor: %s on %s — %s (%.0fms)",
                    action.action_type.value, action.target,
                    outcome.result[:100], elapsed_ms,
                )
            else:
                logger.warning(
                    "GitHub executor: %s on %s FAILED — %s (%.0fms)",
                    action.action_type.value, action.target,
                    outcome.error[:100], elapsed_ms,
                )

        return outcomes

    async def _execute_action(
        self,
        action: PlannedAction,
        observations: dict[str, RepoObservation],
    ) -> ActionOutcome:
        """Execute a single planned action."""
        try:
            if action.action_type == ActionType.MERGE_PR:
                return await self._handle_merge_pr(action, observations)

            elif action.action_type == ActionType.APPROVE_PR:
                return await self._handle_approve_pr(action, observations)

            elif action.action_type == ActionType.REVIEW_PR:
                return await self._handle_review_pr(action, observations)

            elif action.action_type == ActionType.RESPOND_ISSUE:
                return await self._handle_respond_issue(action, observations)

            elif action.action_type == ActionType.NOTIFY_OWNER:
                return await self._handle_notify_owner(action)

            elif action.action_type == ActionType.COMMENT_PR:
                return await self._handle_comment_pr(action)

            elif action.action_type == ActionType.REFRESH_CONTEXT:
                return ActionOutcome(
                    action=action, success=True,
                    result="Context refresh noted (handled by observation phase)",
                )

            elif action.action_type == ActionType.SKIP:
                return ActionOutcome(
                    action=action, success=True,
                    result=f"Skipped: {action.reasoning}",
                )

            else:
                return ActionOutcome(
                    action=action, success=False,
                    error=f"Unknown action type: {action.action_type}",
                )

        except Exception as e:
            logger.error(
                "GitHub executor: unhandled error in %s: %s",
                action.action_type.value, e, exc_info=True,
            )
            return ActionOutcome(
                action=action, success=False,
                error=f"Unhandled error: {e}",
            )

    async def _handle_merge_pr(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> ActionOutcome:
        """Handle merge_pr action."""
        pr = self._find_pr(action, observations)
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

    async def _handle_approve_pr(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> ActionOutcome:
        """Handle approve_pr action."""
        pr = self._find_pr(action, observations)
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

    async def _handle_review_pr(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> ActionOutcome:
        """Handle review_pr action (comment review)."""
        pr = self._find_pr(action, observations)
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

    async def _handle_respond_issue(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> ActionOutcome:
        """Handle respond_issue action."""
        issue = self._find_issue(action, observations)
        if not issue:
            return ActionOutcome(
                action=action, success=False,
                error=f"Issue #{action.target_number} not found in observations",
            )

        return await self._issue_responder.handle_respond(
            action, issue, default_branch=self._default_branch,
        )

    async def _handle_notify_owner(self, action: PlannedAction) -> ActionOutcome:
        """Handle notify_owner action."""
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

    async def _handle_comment_pr(self, action: PlannedAction) -> ActionOutcome:
        """Handle comment_pr action."""
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
            # Use issue comment endpoint (works for PRs too)
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

    def _find_pr(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> Optional[PRSnapshot]:
        """Find a PR in observations by number."""
        obs = observations.get(action.repo)
        if not obs:
            return None
        for pr in obs.open_prs:
            if pr.number == action.target_number:
                return pr
        return None

    def _find_issue(
        self, action: PlannedAction, observations: dict[str, RepoObservation],
    ) -> Optional[IssueSnapshot]:
        """Find an issue in observations by number."""
        obs = observations.get(action.repo)
        if not obs:
            return None
        for issue in obs.open_issues:
            if issue.number == action.target_number:
                return issue
        return None
