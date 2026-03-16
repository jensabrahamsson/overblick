"""
Additional coverage tests for pr_creator module.

Covers uncovered lines:
- 118-129: _run_gh_pr_create edge cases (non-URL output, timeout, file not found, generic error)
"""

from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.dev_agent.models import BugReport, BugSource
from overblick.plugins.dev_agent.pr_creator import PRCreator


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


class TestRunGhPrCreateEdgeCases:
    @pytest.mark.asyncio
    async def test_should_handle_non_url_output(self, tmp_path, bug):
        """Lines 118-119: gh outputs non-URL text."""
        creator = PRCreator(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"some non-url output\n", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url == "some non-url output"

    @pytest.mark.asyncio
    async def test_should_handle_empty_output_on_success(self, tmp_path, bug):
        """Line 119: empty stdout with returncode 0 returns None."""
        creator = PRCreator(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url is None

    @pytest.mark.asyncio
    async def test_should_handle_timeout(self, tmp_path, bug):
        """Lines 121-123: TimeoutError returns None."""
        creator = PRCreator(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=TimeoutError("timed out"))
            mock_exec.return_value = mock_proc

            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url is None

    @pytest.mark.asyncio
    async def test_should_handle_file_not_found(self, tmp_path, bug):
        """Lines 124-125: gh CLI not found returns None."""
        creator = PRCreator(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url is None

    @pytest.mark.asyncio
    async def test_should_handle_generic_exception(self, tmp_path, bug):
        """Lines 127-129: generic exception returns None."""
        creator = PRCreator(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("something broke")):
            url = await creator.create_pr(bug=bug, branch="fix/1-test")
            assert url is None
