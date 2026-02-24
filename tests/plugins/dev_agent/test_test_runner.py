"""Tests for the test runner."""

from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.dev_agent.test_runner import TestRunner


@pytest.fixture
def runner(tmp_path):
    return TestRunner(
        workspace_path=tmp_path,
        timeout=30,
        dry_run=False,
    )


@pytest.fixture
def dry_runner(tmp_path):
    return TestRunner(
        workspace_path=tmp_path,
        dry_run=True,
    )


class TestRunTests:
    @pytest.mark.asyncio
    async def test_dry_run(self, dry_runner):
        result = await dry_runner.run_tests()
        assert result.passed is True
        assert "DRY RUN" in result.output

    @pytest.mark.asyncio
    async def test_success(self, runner):
        output = b"5 passed, 1 skipped in 2.34s\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(output, None))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await runner.run_tests()
            assert result.passed is True
            assert result.total == 6  # 5 passed + 1 skipped
            assert result.skipped == 1

    @pytest.mark.asyncio
    async def test_failure(self, runner):
        output = b"3 passed, 2 failed, 1 error in 5.67s\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(output, None))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = await runner.run_tests()
            assert result.passed is False
            assert result.failures == 2
            assert result.errors == 1
            assert result.total == 6  # 3 + 2 + 1

    @pytest.mark.asyncio
    async def test_with_test_path(self, runner):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"1 passed\n", None))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await runner.run_tests("tests/test_specific.py")

            # Verify test path was passed in command
            call_args = mock_exec.call_args[0]
            assert "tests/test_specific.py" in call_args


class TestParseOutput:
    def test_all_passed(self):
        result = TestRunner._parse_output("10 passed in 1.23s")
        assert result.total == 10
        assert result.failures == 0

    def test_mixed_results(self):
        result = TestRunner._parse_output("5 passed, 2 failed, 1 error, 3 skipped in 5.0s")
        assert result.total == 11
        assert result.failures == 2
        assert result.errors == 1
        assert result.skipped == 3

    def test_no_summary(self):
        result = TestRunner._parse_output("some random output")
        assert result.total == 0

    def test_only_failures(self):
        result = TestRunner._parse_output("3 failed in 2.0s")
        assert result.failures == 3
        assert result.total == 3


class TestBuildCommand:
    def test_default_command(self, runner):
        cmd = runner._build_command("")
        assert "pytest" in cmd
        assert "not llm and not e2e" in cmd

    def test_specific_path(self, runner):
        cmd = runner._build_command("tests/specific.py")
        assert "tests/specific.py" in cmd
