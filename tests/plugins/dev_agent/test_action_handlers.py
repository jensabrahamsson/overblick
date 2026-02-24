"""Tests for dev agent action handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.core.agentic.models import PlannedAction
from overblick.plugins.dev_agent.action_handlers import (
    AnalyzeBugHandler,
    CleanWorkspaceHandler,
    CreatePRHandler,
    FixBugHandler,
    NotifyOwnerHandler,
    RunTestsHandler,
    SkipHandler,
    build_dev_agent_handlers,
)
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    DevAgentObservation,
    FixAttempt,
    OpencodeResult,
    TestRunResult,
)


@pytest.fixture
def action():
    return PlannedAction(
        action_type="analyze_bug",
        target="Bug #1",
        target_number=1,
        reasoning="High priority bug",
    )


class TestAnalyzeBugHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_db, sample_bug, sample_observation):
        opencode = AsyncMock()
        opencode.analyze_bug = AsyncMock(return_value="Root cause: missing check")

        handler = AnalyzeBugHandler(mock_db, opencode)
        action = PlannedAction(action_type="analyze_bug", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is True
        assert "Root cause" in result.result

    @pytest.mark.asyncio
    async def test_bug_not_found(self, mock_db):
        opencode = AsyncMock()
        handler = AnalyzeBugHandler(mock_db, opencode)
        action = PlannedAction(action_type="analyze_bug", target_number=999)
        obs = DevAgentObservation()

        result = await handler.handle(action, obs)
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_bug_not_retriable(self, mock_db, sample_bug_failed):
        opencode = AsyncMock()
        handler = AnalyzeBugHandler(mock_db, opencode)
        action = PlannedAction(action_type="analyze_bug", target_number=2)
        obs = DevAgentObservation(bugs=[sample_bug_failed])

        result = await handler.handle(action, obs)
        assert result.success is False
        assert "not retriable" in result.error


class TestFixBugHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_db, sample_bug, sample_observation):
        workspace = AsyncMock()
        workspace.ensure_cloned = AsyncMock(return_value=True)
        workspace.create_branch = AsyncMock(return_value=True)
        workspace.commit_and_push = AsyncMock(return_value=True)

        opencode = AsyncMock()
        opencode.fix_bug = AsyncMock(return_value=OpencodeResult(
            success=True, output="Fixed", files_changed=["api.py"],
        ))

        test_runner = AsyncMock()
        test_runner.run_tests = AsyncMock(return_value=TestRunResult(
            passed=True, total=10, output="10 passed",
        ))

        handler = FixBugHandler(mock_db, workspace, opencode, test_runner, dry_run=True)
        action = PlannedAction(action_type="fix_bug", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_clone_failure(self, mock_db, sample_bug, sample_observation):
        workspace = AsyncMock()
        workspace.ensure_cloned = AsyncMock(return_value=False)
        opencode = AsyncMock()
        test_runner = AsyncMock()

        handler = FixBugHandler(mock_db, workspace, opencode, test_runner)
        action = PlannedAction(action_type="fix_bug", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is False
        assert "clone" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tests_fail(self, mock_db, sample_bug, sample_observation):
        workspace = AsyncMock()
        workspace.ensure_cloned = AsyncMock(return_value=True)
        workspace.create_branch = AsyncMock(return_value=True)

        opencode = AsyncMock()
        opencode.fix_bug = AsyncMock(return_value=OpencodeResult(success=True))

        test_runner = AsyncMock()
        test_runner.run_tests = AsyncMock(return_value=TestRunResult(
            passed=False, failures=2, output="2 failed",
        ))

        handler = FixBugHandler(mock_db, workspace, opencode, test_runner)
        action = PlannedAction(action_type="fix_bug", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is False
        assert "Tests failed" in result.error


class TestRunTestsHandler:
    @pytest.mark.asyncio
    async def test_success(self):
        test_runner = AsyncMock()
        test_runner.run_tests = AsyncMock(return_value=TestRunResult(
            passed=True, total=5, output="5 passed",
        ))

        handler = RunTestsHandler(test_runner)
        action = PlannedAction(action_type="run_tests")

        result = await handler.handle(action, None)
        assert result.success is True
        assert "PASSED" in result.result


class TestCreatePRHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_db, sample_bug, sample_observation):
        sample_bug.branch_name = "fix/1-api-500"
        mock_db.get_fix_attempts = AsyncMock(return_value=[
            FixAttempt(bug_id=1, tests_passed=True, files_changed=["api.py"]),
        ])

        pr_creator = AsyncMock()
        pr_creator.create_pr = AsyncMock(return_value="https://github.com/test/pr/1")

        handler = CreatePRHandler(mock_db, pr_creator)
        action = PlannedAction(action_type="create_pr", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is True
        assert "pr/1" in result.result

    @pytest.mark.asyncio
    async def test_no_branch(self, mock_db, sample_bug, sample_observation):
        sample_bug.branch_name = ""  # No branch

        handler = CreatePRHandler(mock_db, AsyncMock())
        action = PlannedAction(action_type="create_pr", target_number=1)

        result = await handler.handle(action, sample_observation)
        assert result.success is False
        assert "no branch" in result.error.lower()


class TestNotifyOwnerHandler:
    @pytest.mark.asyncio
    async def test_dry_run(self):
        handler = NotifyOwnerHandler(dry_run=True)
        action = PlannedAction(
            action_type="notify_owner",
            target="Bug #1 fixed",
            reasoning="Important fix",
        )

        result = await handler.handle(action, None)
        assert result.success is True
        assert "DRY RUN" in result.result

    @pytest.mark.asyncio
    async def test_live_success(self):
        notify_fn = AsyncMock()
        handler = NotifyOwnerHandler(notify_fn=notify_fn, dry_run=False)
        action = PlannedAction(action_type="notify_owner", target="Update")

        result = await handler.handle(action, None)
        assert result.success is True
        notify_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notify_fn(self):
        handler = NotifyOwnerHandler(notify_fn=None, dry_run=False)
        action = PlannedAction(action_type="notify_owner", target="Update")

        result = await handler.handle(action, None)
        assert result.success is False


class TestCleanWorkspaceHandler:
    @pytest.mark.asyncio
    async def test_clean_specific_branch(self):
        workspace = AsyncMock()
        handler = CleanWorkspaceHandler(workspace)
        action = PlannedAction(
            action_type="clean_workspace",
            params={"branch": "fix/old-branch"},
        )

        result = await handler.handle(action, None)
        assert result.success is True
        workspace.cleanup_branch.assert_called_once_with("fix/old-branch")

    @pytest.mark.asyncio
    async def test_clean_all_fix_branches(self):
        workspace = AsyncMock()
        workspace.list_branches = AsyncMock(return_value=["main", "fix/1-test", "fix/2-test"])
        workspace._get_current_branch = AsyncMock(return_value="main")

        handler = CleanWorkspaceHandler(workspace)
        action = PlannedAction(action_type="clean_workspace")

        result = await handler.handle(action, None)
        assert result.success is True
        assert "2" in result.result  # Cleaned 2 branches


class TestSkipHandler:
    @pytest.mark.asyncio
    async def test_skip(self):
        handler = SkipHandler()
        action = PlannedAction(
            action_type="skip",
            reasoning="No bugs to fix",
        )

        result = await handler.handle(action, None)
        assert result.success is True
        assert "No bugs to fix" in result.result


class TestBuildDevAgentHandlers:
    def test_builds_all_handlers(self, mock_db, tmp_path):
        from overblick.plugins.dev_agent.opencode_runner import OpencodeRunner
        from overblick.plugins.dev_agent.pr_creator import PRCreator
        from overblick.plugins.dev_agent.test_runner import TestRunner
        from overblick.plugins.dev_agent.workspace import WorkspaceManager

        handlers = build_dev_agent_handlers(
            db=mock_db,
            workspace=MagicMock(spec=WorkspaceManager),
            opencode=MagicMock(spec=OpencodeRunner),
            test_runner=MagicMock(spec=TestRunner),
            pr_creator=MagicMock(spec=PRCreator),
        )

        assert "analyze_bug" in handlers
        assert "fix_bug" in handlers
        assert "run_tests" in handlers
        assert "create_pr" in handlers
        assert "notify_owner" in handlers
        assert "clean_workspace" in handlers
        assert "skip" in handlers
        assert len(handlers) == 7
