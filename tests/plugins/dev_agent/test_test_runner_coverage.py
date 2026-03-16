"""
Additional coverage tests for test_runner module.

Covers uncovered lines:
- 93-111: TimeoutError, FileNotFoundError, and generic Exception handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.dev_agent.test_runner import TestRunner


class TestRunTestsExceptions:
    @pytest.mark.asyncio
    async def test_should_handle_timeout_error(self, tmp_path):
        """Lines 93-104: TimeoutError is caught and handled."""
        runner = TestRunner(workspace_path=tmp_path, timeout=1, dry_run=False)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=TimeoutError("timed out"))
            mock_proc.kill = MagicMock()  # kill() is sync, not async
            mock_exec.return_value = mock_proc

            result = await runner.run_tests()
            assert result.passed is False
            assert "timed out" in result.output

    @pytest.mark.asyncio
    async def test_should_handle_file_not_found_error(self, tmp_path):
        """Lines 105-109: FileNotFoundError when pytest not found."""
        runner = TestRunner(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = await runner.run_tests()
            assert result.passed is False
            assert "pytest not found" in result.output

    @pytest.mark.asyncio
    async def test_should_handle_generic_exception(self, tmp_path):
        """Lines 110-113: generic Exception is caught."""
        runner = TestRunner(workspace_path=tmp_path, dry_run=False)

        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("something broke")):
            result = await runner.run_tests()
            assert result.passed is False
            assert "something broke" in result.output

    @pytest.mark.asyncio
    async def test_should_handle_timeout_with_kill_failure(self, tmp_path):
        """Lines 96-99: kill() failure after timeout is silently caught."""
        runner = TestRunner(workspace_path=tmp_path, timeout=1, dry_run=False)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=TimeoutError("timed out"))
            mock_proc.kill = MagicMock(side_effect=ProcessLookupError("already dead"))
            mock_exec.return_value = mock_proc

            result = await runner.run_tests()
            assert result.passed is False
