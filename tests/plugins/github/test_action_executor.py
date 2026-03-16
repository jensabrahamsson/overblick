"""
Tests for GitHub action handlers — all handler classes and the factory.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.github.action_executor import (
    ApprovePRHandler,
    CommentPRHandler,
    MergePRHandler,
    NotifyOwnerHandler,
    RefreshContextHandler,
    RespondIssueHandler,
    ReviewPRHandler,
    SkipHandler,
    _find_issue,
    _find_pr,
    build_github_handlers,
)
from overblick.plugins.github.models import (
    ActionType,
    CIStatus,
    IssueSnapshot,
    PlannedAction,
    PRSnapshot,
    RepoObservation,
    VersionBumpType,
)


def _make_action(action_type="merge_pr", repo="o/r", target_number=42, reasoning="test"):
    return PlannedAction(
        action_type=action_type,
        target=f"#{target_number}",
        target_number=target_number,
        repo=repo,
        priority=50,
        reasoning=reasoning,
    )


def _make_pr(number=42, is_dependabot=True, version_bump=VersionBumpType.PATCH):
    return PRSnapshot(
        number=number,
        title="Bump foo",
        author="dependabot[bot]" if is_dependabot else "dev",
        is_dependabot=is_dependabot,
        version_bump=version_bump,
        ci_status=CIStatus.SUCCESS,
        mergeable=True,
    )


def _make_issue(number=10):
    return IssueSnapshot(
        number=number,
        title="Bug report",
        author="reporter",
        body="Something broken",
    )


def _make_observation(prs=None, issues=None, repo="o/r"):
    obs = RepoObservation(
        repo=repo,
        open_prs=prs or [],
        open_issues=issues or [],
    )
    return {repo: obs}


class TestFindPr:
    """Test _find_pr helper."""

    def test_should_find_pr_by_number(self):
        pr = _make_pr(42)
        observation = _make_observation(prs=[pr])
        action = _make_action(target_number=42)
        assert _find_pr(action, observation) == pr

    def test_should_return_none_when_not_found(self):
        observation = _make_observation(prs=[_make_pr(99)])
        action = _make_action(target_number=42)
        assert _find_pr(action, observation) is None

    def test_should_return_none_for_non_dict_observation(self):
        action = _make_action()
        assert _find_pr(action, None) is None
        assert _find_pr(action, "string") is None

    def test_should_return_none_for_missing_repo(self):
        action = _make_action(repo="other/repo")
        observation = _make_observation(repo="o/r")
        assert _find_pr(action, observation) is None


class TestFindIssue:
    """Test _find_issue helper."""

    def test_should_find_issue_by_number(self):
        issue = _make_issue(10)
        observation = _make_observation(issues=[issue])
        action = _make_action(target_number=10)
        assert _find_issue(action, observation) == issue

    def test_should_return_none_when_not_found(self):
        observation = _make_observation(issues=[_make_issue(99)])
        action = _make_action(target_number=10)
        assert _find_issue(action, observation) is None

    def test_should_return_none_for_non_dict(self):
        action = _make_action()
        assert _find_issue(action, None) is None

    def test_should_return_none_for_missing_repo(self):
        action = _make_action(repo="other/repo")
        observation = _make_observation(repo="o/r")
        assert _find_issue(action, observation) is None


class TestMergePRHandler:
    """Test MergePRHandler."""

    @pytest.mark.asyncio
    async def test_should_fail_when_pr_not_found(self):
        handler = MergePRHandler(dependabot=AsyncMock())
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation())
        assert outcome.success is False
        assert "not found" in outcome.error

    @pytest.mark.asyncio
    async def test_should_fail_for_non_dependabot(self):
        handler = MergePRHandler(dependabot=AsyncMock())
        pr = _make_pr(42, is_dependabot=False)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is False
        assert "Only Dependabot" in outcome.error

    @pytest.mark.asyncio
    async def test_should_delegate_major_bump_to_review(self):
        mock_dep = AsyncMock()
        mock_dep.review_major_bump = AsyncMock(return_value=MagicMock(success=True))
        handler = MergePRHandler(dependabot=mock_dep)
        pr = _make_pr(42, version_bump=VersionBumpType.MAJOR)
        action = _make_action(target_number=42)
        await handler.handle(action, _make_observation(prs=[pr]))
        mock_dep.review_major_bump.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_delegate_to_handle_merge(self):
        mock_dep = AsyncMock()
        mock_dep.handle_merge = AsyncMock(return_value=MagicMock(success=True))
        handler = MergePRHandler(dependabot=mock_dep)
        pr = _make_pr(42, version_bump=VersionBumpType.PATCH)
        action = _make_action(target_number=42)
        await handler.handle(action, _make_observation(prs=[pr]))
        mock_dep.handle_merge.assert_called_once()


class TestApprovePRHandler:
    """Test ApprovePRHandler."""

    @pytest.mark.asyncio
    async def test_should_fail_when_pr_not_found(self):
        handler = ApprovePRHandler(client=AsyncMock(), dry_run=True)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation())
        assert outcome.success is False
        assert "not found" in outcome.error

    @pytest.mark.asyncio
    async def test_should_dry_run(self):
        handler = ApprovePRHandler(client=AsyncMock(), dry_run=True)
        pr = _make_pr(42)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is True
        assert "DRY RUN" in outcome.result

    @pytest.mark.asyncio
    async def test_should_approve_live(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        handler = ApprovePRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42, reasoning="Looks good")
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is True
        assert "Approved" in outcome.result
        client.create_pull_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_approval_failure(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(side_effect=Exception("API error"))
        handler = ApprovePRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is False
        assert "Failed to approve" in outcome.error

    @pytest.mark.asyncio
    async def test_should_use_default_body_when_no_reasoning(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        handler = ApprovePRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42, reasoning="")
        await handler.handle(action, _make_observation(prs=[pr]))
        call_kwargs = client.create_pull_review.call_args
        assert "Approved by" in call_kwargs[1]["body"]


class TestReviewPRHandler:
    """Test ReviewPRHandler."""

    @pytest.mark.asyncio
    async def test_should_fail_when_pr_not_found(self):
        handler = ReviewPRHandler(client=AsyncMock(), dry_run=True)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation())
        assert outcome.success is False

    @pytest.mark.asyncio
    async def test_should_dry_run(self):
        handler = ReviewPRHandler(client=AsyncMock(), dry_run=True)
        pr = _make_pr(42)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is True
        assert "DRY RUN" in outcome.result

    @pytest.mark.asyncio
    async def test_should_review_live(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        handler = ReviewPRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42, reasoning="Looks good")
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is True
        assert "Reviewed" in outcome.result

    @pytest.mark.asyncio
    async def test_should_handle_review_failure(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(side_effect=Exception("fail"))
        handler = ReviewPRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42)
        outcome = await handler.handle(action, _make_observation(prs=[pr]))
        assert outcome.success is False
        assert "Failed to review" in outcome.error

    @pytest.mark.asyncio
    async def test_should_use_default_body_when_no_reasoning(self):
        client = AsyncMock()
        client.create_pull_review = AsyncMock(return_value={})
        handler = ReviewPRHandler(client=client, dry_run=False)
        pr = _make_pr(42)
        action = _make_action(target_number=42, reasoning="")
        await handler.handle(action, _make_observation(prs=[pr]))
        call_kwargs = client.create_pull_review.call_args
        assert "Reviewed by" in call_kwargs[1]["body"]


class TestRespondIssueHandler:
    """Test RespondIssueHandler."""

    @pytest.mark.asyncio
    async def test_should_fail_when_issue_not_found(self):
        handler = RespondIssueHandler(issue_responder=AsyncMock())
        action = _make_action(target_number=10)
        outcome = await handler.handle(action, _make_observation())
        assert outcome.success is False
        assert "not found" in outcome.error

    @pytest.mark.asyncio
    async def test_should_delegate_to_issue_responder(self):
        mock_responder = AsyncMock()
        mock_responder.handle_respond = AsyncMock(return_value=MagicMock(success=True))
        handler = RespondIssueHandler(issue_responder=mock_responder, default_branch="develop")
        issue = _make_issue(10)
        action = _make_action(target_number=10)
        await handler.handle(action, _make_observation(issues=[issue]))
        mock_responder.handle_respond.assert_called_once_with(action, issue, default_branch="develop")


class TestNotifyOwnerHandler:
    """Test NotifyOwnerHandler."""

    @pytest.mark.asyncio
    async def test_should_dry_run(self):
        handler = NotifyOwnerHandler(notify_fn=AsyncMock(), dry_run=True)
        action = _make_action(action_type="notify_owner")
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "DRY RUN" in outcome.result

    @pytest.mark.asyncio
    async def test_should_notify_live(self):
        mock_fn = AsyncMock()
        handler = NotifyOwnerHandler(notify_fn=mock_fn, dry_run=False)
        action = _make_action(action_type="notify_owner")
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "Notified owner" in outcome.result
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_notification_failure(self):
        mock_fn = AsyncMock(side_effect=Exception("send failed"))
        handler = NotifyOwnerHandler(notify_fn=mock_fn, dry_run=False)
        action = _make_action(action_type="notify_owner")
        outcome = await handler.handle(action, None)
        assert outcome.success is False
        assert "Notification failed" in outcome.error

    @pytest.mark.asyncio
    async def test_should_fail_without_notify_fn(self):
        handler = NotifyOwnerHandler(notify_fn=None, dry_run=False)
        action = _make_action(action_type="notify_owner")
        outcome = await handler.handle(action, None)
        assert outcome.success is False
        assert "No notification function" in outcome.error


class TestCommentPRHandler:
    """Test CommentPRHandler."""

    @pytest.mark.asyncio
    async def test_should_dry_run(self):
        handler = CommentPRHandler(client=AsyncMock(), dry_run=True)
        action = _make_action(action_type="comment_pr", target_number=42)
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "DRY RUN" in outcome.result

    @pytest.mark.asyncio
    async def test_should_comment_live(self):
        client = AsyncMock()
        client.create_comment = AsyncMock(return_value={"id": 1})
        handler = CommentPRHandler(client=client, dry_run=False)
        action = _make_action(action_type="comment_pr", target_number=42, reasoning="Nice work")
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "Commented" in outcome.result

    @pytest.mark.asyncio
    async def test_should_use_params_body_over_reasoning(self):
        client = AsyncMock()
        client.create_comment = AsyncMock(return_value={"id": 1})
        handler = CommentPRHandler(client=client, dry_run=False)
        action = PlannedAction(
            action_type="comment_pr",
            target_number=42,
            repo="o/r",
            reasoning="fallback",
            params={"body": "primary body"},
        )
        await handler.handle(action, None)
        client.create_comment.assert_called_once_with("o/r", 42, "primary body")

    @pytest.mark.asyncio
    async def test_should_fail_without_body(self):
        handler = CommentPRHandler(client=AsyncMock(), dry_run=False)
        action = PlannedAction(
            action_type="comment_pr",
            target_number=42,
            repo="o/r",
            reasoning="",
            params={},
        )
        outcome = await handler.handle(action, None)
        assert outcome.success is False
        assert "No comment body" in outcome.error

    @pytest.mark.asyncio
    async def test_should_handle_comment_failure(self):
        client = AsyncMock()
        client.create_comment = AsyncMock(side_effect=Exception("API error"))
        handler = CommentPRHandler(client=client, dry_run=False)
        action = _make_action(action_type="comment_pr", reasoning="test body")
        outcome = await handler.handle(action, None)
        assert outcome.success is False
        assert "Failed to comment" in outcome.error


class TestRefreshContextHandler:
    """Test RefreshContextHandler."""

    @pytest.mark.asyncio
    async def test_should_always_succeed(self):
        handler = RefreshContextHandler()
        action = _make_action(action_type="refresh_context")
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "handled by observation" in outcome.result


class TestSkipHandler:
    """Test SkipHandler."""

    @pytest.mark.asyncio
    async def test_should_succeed_with_reasoning(self):
        handler = SkipHandler()
        action = _make_action(action_type="skip", reasoning="Nothing to do")
        outcome = await handler.handle(action, None)
        assert outcome.success is True
        assert "Nothing to do" in outcome.result


class TestBuildGithubHandlers:
    """Test the handler factory function."""

    def test_should_create_all_handler_types(self):
        handlers = build_github_handlers(
            client=AsyncMock(),
            dependabot=AsyncMock(),
            issue_responder=AsyncMock(),
            notify_fn=AsyncMock(),
            dry_run=True,
        )
        assert ActionType.MERGE_PR.value in handlers
        assert ActionType.APPROVE_PR.value in handlers
        assert ActionType.REVIEW_PR.value in handlers
        assert ActionType.RESPOND_ISSUE.value in handlers
        assert ActionType.NOTIFY_OWNER.value in handlers
        assert ActionType.COMMENT_PR.value in handlers
        assert ActionType.REFRESH_CONTEXT.value in handlers
        assert ActionType.SKIP.value in handlers
        assert len(handlers) == 8
