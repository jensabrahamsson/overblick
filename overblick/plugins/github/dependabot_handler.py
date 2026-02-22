"""
DependabotHandler — review and auto-merge safe Dependabot PRs.

Logic:
- Patch/minor bump + CI passing + mergeable -> APPROVE + SQUASH MERGE
- Major bump -> Notify owner (don't auto-merge)
- CI failing -> Notify owner
- Draft PRs -> Skip
"""

import json
import logging
from typing import Optional

from overblick.plugins.github.client import GitHubAPIClient, GitHubAPIError
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import (
    ActionOutcome,
    ActionType,
    CIStatus,
    PlannedAction,
    PRSnapshot,
    VersionBumpType,
)
from overblick.plugins.github.prompts import dependabot_review_prompt

logger = logging.getLogger(__name__)


class DependabotHandler:
    """
    Handles Dependabot PR review and auto-merge.

    Safety-first approach:
    1. Only merges patch/minor bumps
    2. Requires ALL CI checks to pass
    3. PR must be mergeable (no conflicts)
    4. LLM reviews diff for major bumps
    5. Notifies owner for anything non-trivial
    """

    def __init__(
        self,
        client: GitHubAPIClient,
        db: GitHubDB,
        llm_pipeline=None,
        system_prompt: str = "",
        auto_merge_patch: bool = True,
        auto_merge_minor: bool = True,
        auto_merge_major: bool = False,
        require_ci_pass: bool = True,
        dry_run: bool = True,
    ):
        self._client = client
        self._db = db
        self._llm_pipeline = llm_pipeline
        self._system_prompt = system_prompt
        self._auto_merge_patch = auto_merge_patch
        self._auto_merge_minor = auto_merge_minor
        self._auto_merge_major = auto_merge_major
        self._require_ci_pass = require_ci_pass
        self._dry_run = dry_run

    async def handle_merge(
        self,
        action: PlannedAction,
        pr: PRSnapshot,
    ) -> ActionOutcome:
        """
        Attempt to merge a Dependabot PR.

        Validates all safety checks before merging.
        """
        repo = action.repo

        # Safety check: must be Dependabot
        if not pr.is_dependabot:
            return ActionOutcome(
                action=action,
                success=False,
                error="Not a Dependabot PR — refusing to merge",
            )

        # Safety check: draft PRs
        if pr.draft:
            return ActionOutcome(
                action=action,
                success=False,
                error="PR is a draft — skipping",
            )

        # Safety check: CI must pass
        if self._require_ci_pass and pr.ci_status != CIStatus.SUCCESS:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"CI not passing (status: {pr.ci_status.value})",
            )

        # Safety check: must be mergeable
        if not pr.mergeable:
            return ActionOutcome(
                action=action,
                success=False,
                error="PR has merge conflicts or is not mergeable",
            )

        # Safety check: version bump type
        allowed = self._is_bump_allowed(pr.version_bump)
        if not allowed:
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Version bump type '{pr.version_bump.value}' not allowed for auto-merge",
            )

        # Check if already auto-merged by us
        if await self._db.was_pr_auto_merged(repo, pr.number):
            return ActionOutcome(
                action=action,
                success=False,
                error="PR was already auto-merged by us",
            )

        # Dry run mode
        if self._dry_run:
            logger.info(
                "DRY RUN: would merge PR #%d in %s (%s %s bump, ci:%s)",
                pr.number, repo, pr.version_bump.value,
                "dependabot" if pr.is_dependabot else "other",
                pr.ci_status.value,
            )
            return ActionOutcome(
                action=action,
                success=True,
                result=f"DRY RUN: would merge PR #{pr.number} ({pr.version_bump.value} bump)",
            )

        # Approve the PR first
        try:
            await self._client.create_pull_review(
                repo, pr.number,
                event="APPROVE",
                body=(
                    f"Auto-approved by Överblick agent. "
                    f"{pr.version_bump.value.capitalize()} version bump, "
                    f"all CI checks passing."
                ),
            )
        except GitHubAPIError as e:
            logger.warning("Failed to approve PR #%d: %s", pr.number, e)
            # Continue with merge attempt even if approval fails

        # Merge the PR
        try:
            merge_result = await self._client.merge_pull(
                repo, pr.number,
                merge_method="squash",
                commit_title=pr.title,
            )

            # Record in database
            await self._db.upsert_pr_tracking(
                repo, pr.number,
                merged=True,
                auto_merged=True,
                ci_status=pr.ci_status.value,
            )

            logger.info(
                "Auto-merged Dependabot PR #%d in %s (%s bump)",
                pr.number, repo, pr.version_bump.value,
            )

            return ActionOutcome(
                action=action,
                success=True,
                result=f"Merged PR #{pr.number}: {pr.title}",
            )

        except GitHubAPIError as e:
            logger.error(
                "Failed to merge Dependabot PR #%d in %s: %s",
                pr.number, repo, e,
            )
            return ActionOutcome(
                action=action,
                success=False,
                error=f"Merge failed: {e}",
            )

    async def review_major_bump(
        self,
        action: PlannedAction,
        pr: PRSnapshot,
    ) -> ActionOutcome:
        """
        Review a major Dependabot bump using LLM analysis.

        Does not auto-merge — produces a review summary for owner notification.
        """
        repo = action.repo

        if not self._llm_pipeline:
            return ActionOutcome(
                action=action,
                success=True,
                result=f"Major bump PR #{pr.number}: {pr.title} (no LLM for review)",
            )

        # Fetch diff for analysis
        try:
            diff = await self._client.get_pull_diff(repo, pr.number)
        except GitHubAPIError:
            diff = "(diff unavailable)"

        # Get repo summary
        summary_data = await self._db.get_repo_summary(repo)
        repo_summary = summary_data.get("summary", "") if summary_data else ""

        # LLM review
        messages = dependabot_review_prompt(
            system_prompt=self._system_prompt,
            pr_title=pr.title,
            pr_diff=diff,
            version_bump=pr.version_bump.value,
            ci_status=pr.ci_status.value,
            repo_summary=repo_summary,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_dependabot_review",
                skip_preflight=True,
                complexity="ultra",
                priority="low",
            )

            review_text = result.content.strip() if result and result.content else ""
            review_data = self._parse_review(review_text)

            return ActionOutcome(
                action=action,
                success=True,
                result=(
                    f"Reviewed major bump PR #{pr.number}: {pr.title}\n"
                    f"Safe to merge: {review_data.get('safe_to_merge', 'unknown')}\n"
                    f"Reasoning: {review_data.get('reasoning', 'no analysis')}"
                ),
            )

        except Exception as e:
            logger.error("Dependabot review failed: %s", e)
            return ActionOutcome(
                action=action,
                success=False,
                error=f"LLM review failed: {e}",
            )

    def _is_bump_allowed(self, bump: VersionBumpType) -> bool:
        """Check if a version bump type is allowed for auto-merge."""
        if bump == VersionBumpType.PATCH:
            return self._auto_merge_patch
        if bump == VersionBumpType.MINOR:
            return self._auto_merge_minor
        if bump == VersionBumpType.MAJOR:
            return self._auto_merge_major
        return False

    @staticmethod
    def _parse_review(raw: str) -> dict:
        """Parse LLM review response."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

        return {"reasoning": raw[:500]}
