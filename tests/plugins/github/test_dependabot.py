"""
Tests for DependabotHandler — auto-merge logic and safety guards.
"""

import pytest
from unittest.mock import AsyncMock

from overblick.plugins.github.dependabot_handler import DependabotHandler
from overblick.plugins.github.models import (
    ActionType,
    CIStatus,
    PlannedAction,
    PRSnapshot,
    VersionBumpType,
)


@pytest.fixture
def merge_action():
    return PlannedAction(
        action_type=ActionType.MERGE_PR,
        target="PR #42",
        target_number=42,
        repo="owner/repo",
        priority=90,
        reasoning="Safe patch bump",
    )


@pytest.fixture
def safe_pr():
    return PRSnapshot(
        number=42,
        title="Bump lodash from 4.17.20 to 4.17.21",
        author="dependabot[bot]",
        is_dependabot=True,
        version_bump=VersionBumpType.PATCH,
        ci_status=CIStatus.SUCCESS,
        mergeable=True,
    )


@pytest.fixture
def handler():
    return DependabotHandler(
        client=AsyncMock(),
        db=AsyncMock(),
        auto_merge_patch=True,
        auto_merge_minor=True,
        auto_merge_major=False,
        require_ci_pass=True,
        dry_run=True,
    )


class TestDependabotHandler:
    """Test Dependabot merge safety guards."""

    @pytest.mark.asyncio
    async def test_dry_run_succeeds(self, handler, merge_action, safe_pr):
        """Dry run reports success without actually merging."""
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, safe_pr)

        assert outcome.success is True
        assert "DRY RUN" in outcome.result

    @pytest.mark.asyncio
    async def test_rejects_non_dependabot(self, handler, merge_action):
        """Refuses to merge non-Dependabot PRs."""
        pr = PRSnapshot(
            number=42, title="Feature", author="developer",
            is_dependabot=False, ci_status=CIStatus.SUCCESS, mergeable=True,
        )

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "Not a Dependabot PR" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_failing_ci(self, handler, merge_action):
        """Refuses to merge when CI is failing."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.FAILURE, mergeable=True,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "CI not passing" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_unmergeable(self, handler, merge_action):
        """Refuses to merge when PR has conflicts."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS, mergeable=False,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "not mergeable" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_major_bump(self, handler, merge_action):
        """Refuses to auto-merge major version bumps."""
        pr = PRSnapshot(
            number=42, title="Bump pydantic from 1.0 to 2.0",
            author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.MAJOR,
            ci_status=CIStatus.SUCCESS, mergeable=True,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "not allowed" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_draft(self, handler, merge_action):
        """Refuses to merge draft PRs."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS, mergeable=True, draft=True,
        )

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "draft" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_already_merged(self, handler, merge_action, safe_pr):
        """Refuses to merge if already auto-merged by us."""
        handler._db.was_pr_auto_merged = AsyncMock(return_value=True)

        outcome = await handler.handle_merge(merge_action, safe_pr)

        assert outcome.success is False
        assert "already auto-merged" in outcome.error

    @pytest.mark.asyncio
    async def test_live_merge(self, merge_action, safe_pr):
        """Live merge calls GitHub API."""
        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        client.merge_pull = AsyncMock(return_value={"merged": True})

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)
        db.upsert_pr_tracking = AsyncMock()

        handler = DependabotHandler(
            client=client, db=db,
            auto_merge_patch=True, auto_merge_minor=True,
            require_ci_pass=True, dry_run=False,
        )

        outcome = await handler.handle_merge(merge_action, safe_pr)

        assert outcome.success is True
        assert "Merged" in outcome.result
        client.merge_pull.assert_called_once_with(
            "owner/repo", 42, merge_method="squash", commit_title=safe_pr.title,
        )
