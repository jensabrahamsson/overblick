"""
Tests for DependabotHandler — auto-merge logic and safety guards.
"""

from unittest.mock import AsyncMock

import pytest

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
            number=42,
            title="Feature",
            author="developer",
            is_dependabot=False,
            ci_status=CIStatus.SUCCESS,
            mergeable=True,
        )

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "Not a Dependabot PR" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_failing_ci(self, handler, merge_action):
        """Refuses to merge when CI is failing."""
        pr = PRSnapshot(
            number=42,
            title="Bump",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.FAILURE,
            mergeable=True,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "CI not passing" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_unmergeable(self, handler, merge_action):
        """Refuses to merge when PR has conflicts."""
        pr = PRSnapshot(
            number=42,
            title="Bump",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS,
            mergeable=False,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "not mergeable" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_major_bump(self, handler, merge_action):
        """Refuses to auto-merge major version bumps."""
        pr = PRSnapshot(
            number=42,
            title="Bump pydantic from 1.0 to 2.0",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.MAJOR,
            ci_status=CIStatus.SUCCESS,
            mergeable=True,
        )
        handler._db.was_pr_auto_merged = AsyncMock(return_value=False)

        outcome = await handler.handle_merge(merge_action, pr)

        assert outcome.success is False
        assert "not allowed" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_draft(self, handler, merge_action):
        """Refuses to merge draft PRs."""
        pr = PRSnapshot(
            number=42,
            title="Bump",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.SUCCESS,
            mergeable=True,
            draft=True,
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
            client=client,
            db=db,
            auto_merge_patch=True,
            auto_merge_minor=True,
            require_ci_pass=True,
            dry_run=False,
        )

        outcome = await handler.handle_merge(merge_action, safe_pr)

        assert outcome.success is True
        assert "Merged" in outcome.result
        client.merge_pull.assert_called_once_with(
            "owner/repo",
            42,
            merge_method="squash",
            commit_title=safe_pr.title,
        )

    @pytest.mark.asyncio
    async def test_live_merge_approve_failure_continues(self, merge_action, safe_pr):
        """Live merge continues even if approve fails."""
        from overblick.plugins.github.client import GitHubAPIError

        client = AsyncMock()
        client.create_pull_review = AsyncMock(side_effect=GitHubAPIError("forbidden"))
        client.merge_pull = AsyncMock(return_value={"merged": True})

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)
        db.upsert_pr_tracking = AsyncMock()

        handler = DependabotHandler(
            client=client, db=db,
            auto_merge_patch=True, dry_run=False,
        )
        outcome = await handler.handle_merge(merge_action, safe_pr)
        assert outcome.success is True
        assert "Merged" in outcome.result

    @pytest.mark.asyncio
    async def test_live_merge_failure(self, merge_action, safe_pr):
        """Live merge returns failure when merge API call fails."""
        from overblick.plugins.github.client import GitHubAPIError

        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        client.merge_pull = AsyncMock(side_effect=GitHubAPIError("conflict"))

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)
        db.upsert_pr_tracking = AsyncMock()

        handler = DependabotHandler(
            client=client, db=db,
            auto_merge_patch=True, dry_run=False,
        )
        outcome = await handler.handle_merge(merge_action, safe_pr)
        assert outcome.success is False
        assert "Merge failed" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_minor_when_disabled(self, merge_action):
        """Refuses to merge minor bumps when auto_merge_minor=False."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.MINOR,
            ci_status=CIStatus.SUCCESS, mergeable=True,
        )

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)

        handler = DependabotHandler(
            client=AsyncMock(), db=db,
            auto_merge_patch=True, auto_merge_minor=False,
            require_ci_pass=True, dry_run=True,
        )
        outcome = await handler.handle_merge(merge_action, pr)
        assert outcome.success is False
        assert "not allowed" in outcome.error

    @pytest.mark.asyncio
    async def test_rejects_unknown_bump(self, merge_action):
        """Refuses to merge unknown version bump types."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.UNKNOWN,
            ci_status=CIStatus.SUCCESS, mergeable=True,
        )

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)

        handler = DependabotHandler(
            client=AsyncMock(), db=db,
            auto_merge_patch=True, auto_merge_minor=True,
            require_ci_pass=True, dry_run=True,
        )
        outcome = await handler.handle_merge(merge_action, pr)
        assert outcome.success is False
        assert "not allowed" in outcome.error

    @pytest.mark.asyncio
    async def test_passes_ci_check_when_disabled(self, merge_action, safe_pr):
        """Merge proceeds when require_ci_pass=False even with failing CI."""
        pr = PRSnapshot(
            number=42, title="Bump", author="dependabot[bot]",
            is_dependabot=True, version_bump=VersionBumpType.PATCH,
            ci_status=CIStatus.FAILURE, mergeable=True,
        )

        db = AsyncMock()
        db.was_pr_auto_merged = AsyncMock(return_value=False)

        handler = DependabotHandler(
            client=AsyncMock(), db=db,
            auto_merge_patch=True, require_ci_pass=False, dry_run=True,
        )
        outcome = await handler.handle_merge(merge_action, pr)
        assert outcome.success is True
        assert "DRY RUN" in outcome.result


class TestReviewMajorBump:
    """Test review_major_bump method."""

    @pytest.fixture
    def major_pr(self):
        return PRSnapshot(
            number=42,
            title="Bump pydantic from 1.0.0 to 2.0.0",
            author="dependabot[bot]",
            is_dependabot=True,
            version_bump=VersionBumpType.MAJOR,
            ci_status=CIStatus.SUCCESS,
            mergeable=True,
        )

    @pytest.fixture
    def review_action(self):
        return PlannedAction(
            action_type=ActionType.MERGE_PR,
            target="PR #42",
            target_number=42,
            repo="owner/repo",
            reasoning="Major bump needs review",
        )

    @pytest.mark.asyncio
    async def test_should_return_no_llm_message(self, review_action, major_pr):
        """Returns a result without LLM when pipeline is not set."""
        handler = DependabotHandler(
            client=AsyncMock(), db=AsyncMock(),
            llm_pipeline=None, dry_run=True,
        )
        outcome = await handler.review_major_bump(review_action, major_pr)
        assert outcome.success is True
        assert "no LLM" in outcome.result

    @pytest.mark.asyncio
    async def test_should_review_with_llm(self, review_action, major_pr):
        """Reviews using LLM and returns analysis."""
        from overblick.core.llm.pipeline import PipelineResult

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"safe_to_merge": false, "reasoning": "Major bump with breaking changes"}',
        ))

        mock_db = AsyncMock()
        mock_db.get_repo_summary = AsyncMock(return_value={"summary": "Python project"})

        mock_client = AsyncMock()
        mock_client.get_pull_diff = AsyncMock(return_value="diff content")

        handler = DependabotHandler(
            client=mock_client, db=mock_db,
            llm_pipeline=mock_pipeline,
            system_prompt="You are a reviewer",
            dry_run=True,
        )
        outcome = await handler.review_major_bump(review_action, major_pr)
        assert outcome.success is True
        assert "Safe to merge" in outcome.result

    @pytest.mark.asyncio
    async def test_should_handle_diff_fetch_failure(self, review_action, major_pr):
        """Falls back gracefully when diff fetch fails."""
        from overblick.core.llm.pipeline import PipelineResult
        from overblick.plugins.github.client import GitHubAPIError

        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=PipelineResult(
            content='{"safe_to_merge": true, "reasoning": "Looks ok"}',
        ))

        mock_client = AsyncMock()
        mock_client.get_pull_diff = AsyncMock(side_effect=GitHubAPIError("not found"))

        mock_db = AsyncMock()
        mock_db.get_repo_summary = AsyncMock(return_value=None)

        handler = DependabotHandler(
            client=mock_client, db=mock_db,
            llm_pipeline=mock_pipeline, dry_run=True,
        )
        outcome = await handler.review_major_bump(review_action, major_pr)
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_should_handle_llm_failure(self, review_action, major_pr):
        """Returns failure when LLM raises exception."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(side_effect=Exception("LLM error"))

        mock_client = AsyncMock()
        mock_client.get_pull_diff = AsyncMock(return_value="diff")

        mock_db = AsyncMock()
        mock_db.get_repo_summary = AsyncMock(return_value=None)

        handler = DependabotHandler(
            client=mock_client, db=mock_db,
            llm_pipeline=mock_pipeline, dry_run=True,
        )
        outcome = await handler.review_major_bump(review_action, major_pr)
        assert outcome.success is False
        assert "LLM review failed" in outcome.error


class TestParseReview:
    """Test _parse_review static method."""

    def test_should_parse_valid_json(self):
        result = DependabotHandler._parse_review('{"safe_to_merge": true}')
        assert result["safe_to_merge"] is True

    def test_should_parse_wrapped_json(self):
        raw = 'Some text {"safe_to_merge": false} end'
        result = DependabotHandler._parse_review(raw)
        assert result["safe_to_merge"] is False

    def test_should_return_raw_on_invalid_json(self):
        result = DependabotHandler._parse_review("just some analysis text")
        assert "reasoning" in result
        assert "just some analysis" in result["reasoning"]

    def test_should_handle_invalid_wrapped_json(self):
        raw = "text {invalid json} end"
        result = DependabotHandler._parse_review(raw)
        assert "reasoning" in result


class TestIsBumpAllowed:
    """Test _is_bump_allowed method."""

    def test_should_allow_minor_when_enabled(self):
        handler = DependabotHandler(
            client=AsyncMock(), db=AsyncMock(),
            auto_merge_minor=True,
        )
        assert handler._is_bump_allowed(VersionBumpType.MINOR) is True

    def test_should_disallow_major_by_default(self):
        handler = DependabotHandler(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert handler._is_bump_allowed(VersionBumpType.MAJOR) is False

    def test_should_allow_major_when_enabled(self):
        handler = DependabotHandler(
            client=AsyncMock(), db=AsyncMock(),
            auto_merge_major=True,
        )
        assert handler._is_bump_allowed(VersionBumpType.MAJOR) is True

    def test_should_disallow_unknown(self):
        handler = DependabotHandler(
            client=AsyncMock(), db=AsyncMock(),
        )
        assert handler._is_bump_allowed(VersionBumpType.UNKNOWN) is False
