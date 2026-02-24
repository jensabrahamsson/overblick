"""Tests for opencode runner (mocked subprocess)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.dev_agent.models import BugReport, BugSource
from overblick.plugins.dev_agent.opencode_runner import OpencodeRunner


@pytest.fixture
def runner(tmp_path):
    return OpencodeRunner(
        workspace_path=tmp_path,
        model="test-model",
        timeout=30,
        dry_run=False,
    )


@pytest.fixture
def dry_runner(tmp_path):
    return OpencodeRunner(
        workspace_path=tmp_path,
        model="test-model",
        dry_run=True,
    )


@pytest.fixture
def bug():
    return BugReport(
        id=1,
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="API 500 error",
        description="Crash on empty input",
        error_text="TypeError: NoneType\n  at api.py:42",
        file_path="api.py",
    )


class TestAnalyzeBug:
    @pytest.mark.asyncio
    async def test_dry_run(self, dry_runner, bug):
        result = await dry_runner.analyze_bug(bug)
        assert "DRY RUN" in result

    @pytest.mark.asyncio
    async def test_success(self, runner, bug):
        mock_result = json.dumps({"output": "Root cause: missing None check"})

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(mock_result.encode(), b"")
            )
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await runner.analyze_bug(bug)
            assert "None check" in result

    @pytest.mark.asyncio
    async def test_failure(self, runner, bug):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"Error occurred")
            )
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = await runner.analyze_bug(bug)
            assert "failed" in result.lower()


class TestFixBug:
    @pytest.mark.asyncio
    async def test_dry_run(self, dry_runner, bug):
        result = await dry_runner.fix_bug(bug, "analysis text")
        assert result.success is True
        assert "DRY RUN" in result.output

    @pytest.mark.asyncio
    async def test_success_json(self, runner, bug):
        mock_result = json.dumps({
            "output": "Fixed the bug",
            "files_changed": ["api.py"],
        })

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(mock_result.encode(), b"")
            )
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await runner.fix_bug(bug, "analysis")
            assert result.success is True
            assert "api.py" in result.files_changed

    @pytest.mark.asyncio
    async def test_plain_text_output(self, runner, bug):
        """Test fallback to plain text when JSON parsing fails."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"Not JSON output", b"")
            )
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await runner.fix_bug(bug, "analysis")
            assert result.success is True
            assert result.output == "Not JSON output"


class TestParseOutput:
    def test_json_dict(self, runner):
        raw = json.dumps({"output": "hello", "files": ["a.py"]})
        result = runner._parse_output(raw, 1.0)
        assert result.success
        assert result.output == "hello"
        assert "a.py" in result.files_changed

    def test_json_string(self, runner):
        raw = json.dumps("just a string")
        result = runner._parse_output(raw, 2.0)
        assert result.success
        assert result.output == "just a string"

    def test_invalid_json(self, runner):
        result = runner._parse_output("not json", 0.5)
        assert result.success
        assert result.output == "not json"

    def test_files_as_string(self, runner):
        raw = json.dumps({"output": "ok", "files": "single.py"})
        result = runner._parse_output(raw, 1.0)
        assert result.files_changed == ["single.py"]


class TestBuildPrompts:
    def test_analysis_prompt(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="Test",
            description="Desc",
            error_text="Error text",
            file_path="test.py",
        )
        prompt = OpencodeRunner._build_analysis_prompt(bug)
        assert "Test" in prompt
        assert "Desc" in prompt
        assert "Error text" in prompt
        assert "test.py" in prompt
        assert "Do NOT make any code changes" in prompt

    def test_fix_prompt(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="Test",
        )
        prompt = OpencodeRunner._build_fix_prompt(bug, "Root cause found")
        assert "Test" in prompt
        assert "Root cause found" in prompt
        assert "minimal" in prompt.lower()
