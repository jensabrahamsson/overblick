"""Tests for PR creator."""

from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.dev_agent.models import BugReport, BugSource
from overblick.plugins.dev_agent.pr_creator import PRCreator


@pytest.fixture
def creator(tmp_path):
    return PRCreator(
        workspace_path=tmp_path,
        default_branch="main",
        dry_run=False,
    )


@pytest.fixture
def dry_creator(tmp_path):
    return PRCreator(
        workspace_path=tmp_path,
        default_branch="main",
        dry_run=True,
    )


@pytest.fixture
def bug():
    return BugReport(
        id=1,
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="API 500 error",
        description="Server crashes",
        priority=70,
        fix_attempts=1,
    )


class TestCreatePR:
    @pytest.mark.asyncio
    async def test_dry_run(self, dry_creator, bug):
        url = await dry_creator.create_pr(
            bug=bug,
            branch="fix/1-api-500",
        )
        assert url is not None
        assert "dry-run" in url

    @pytest.mark.asyncio
    async def test_success(self, creator, bug):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"https://github.com/test/repo/pull/1\n", b"")
            )
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            url = await creator.create_pr(
                bug=bug,
                branch="fix/1-api-500",
                files_changed=["api.py"],
                test_summary="All tests passed",
            )
            assert url == "https://github.com/test/repo/pull/1"

    @pytest.mark.asyncio
    async def test_failure(self, creator, bug):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"Not found")
            )
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url is None

    @pytest.mark.asyncio
    async def test_title_truncation(self, creator, bug):
        bug.title = "A" * 100  # Very long title

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"https://github.com/test/pr/1\n", b"")
            )
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await creator.create_pr(bug=bug, branch="fix/1-test")

            # Verify title was truncated in command args
            call_args = mock_exec.call_args[0]
            title_idx = list(call_args).index("--title") + 1
            title = call_args[title_idx]
            assert len(title) <= 70


class TestBuildPRBody:
    def test_basic_body(self, bug):
        body = PRCreator._build_pr_body(bug, [], "")
        assert "API 500 error" in body
        assert "github_issue" in body
        assert "Smed" in body

    def test_with_files(self, bug):
        body = PRCreator._build_pr_body(bug, ["api.py", "test.py"], "")
        assert "api.py" in body
        assert "test.py" in body

    def test_with_test_summary(self, bug):
        body = PRCreator._build_pr_body(bug, [], "All 10 tests passed")
        assert "All 10 tests passed" in body
