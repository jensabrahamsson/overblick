"""Tests for dev agent data models."""

import pytest

from overblick.plugins.dev_agent.models import (
    ActionType,
    BugReport,
    BugSource,
    BugStatus,
    DevAgentObservation,
    FixAttempt,
    LogErrorEntry,
    OpencodeResult,
    TestRunResult,
    WorkspaceState,
)


class TestBugReport:
    """Tests for BugReport model."""

    def test_create_minimal(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="Test bug",
        )
        assert bug.status == BugStatus.NEW
        assert bug.priority == 50
        assert bug.fix_attempts == 0
        assert bug.max_attempts == 3

    def test_is_retriable_new(self, sample_bug):
        assert sample_bug.is_retriable is True

    def test_is_retriable_failed_exhausted(self, sample_bug_failed):
        assert sample_bug_failed.is_retriable is False

    def test_is_retriable_after_one_attempt(self):
        bug = BugReport(
            source=BugSource.LOG_ERROR,
            title="Test",
            status=BugStatus.FAILED,
            fix_attempts=1,
            max_attempts=3,
        )
        assert bug.is_retriable is True

    def test_is_retriable_fixed_status(self):
        bug = BugReport(
            source=BugSource.LOG_ERROR,
            title="Test",
            status=BugStatus.FIXED,
            fix_attempts=1,
        )
        assert bug.is_retriable is False

    def test_slug_generation(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="API returns 500 on empty input",
        )
        assert bug.slug == "api-returns-500-on"

    def test_slug_empty_title(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="",
        )
        assert bug.slug == "unknown"

    def test_slug_special_chars(self):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            title="Fix: module.py — crash!",
        )
        # Only alphanumeric words kept
        slug = bug.slug
        assert "-" not in slug or all(c.isalnum() or c == "-" for c in slug)


class TestBugSource:
    """Tests for BugSource enum."""

    def test_all_sources(self):
        assert BugSource.GITHUB_ISSUE.value == "github_issue"
        assert BugSource.LOG_ERROR.value == "log_error"
        assert BugSource.IPC_REPORT.value == "ipc_report"


class TestBugStatus:
    """Tests for BugStatus enum."""

    def test_lifecycle_statuses(self):
        assert BugStatus.NEW.value == "new"
        assert BugStatus.ANALYZING.value == "analyzing"
        assert BugStatus.FIXING.value == "fixing"
        assert BugStatus.TESTING.value == "testing"
        assert BugStatus.PR_CREATED.value == "pr_created"
        assert BugStatus.FIXED.value == "fixed"
        assert BugStatus.FAILED.value == "failed"
        assert BugStatus.SKIPPED.value == "skipped"


class TestFixAttempt:
    """Tests for FixAttempt model."""

    def test_create(self, sample_fix_attempt):
        assert sample_fix_attempt.bug_id == 1
        assert sample_fix_attempt.attempt_number == 1
        assert sample_fix_attempt.tests_passed is True
        assert len(sample_fix_attempt.files_changed) == 2


class TestLogErrorEntry:
    """Tests for LogErrorEntry model."""

    def test_source_ref(self, sample_log_error):
        ref = sample_log_error.source_ref
        assert "anomal" in ref
        assert "agent.log" in ref

    def test_source_ref_format(self):
        entry = LogErrorEntry(
            file_path="/data/test/logs/app.log",
            identity="test",
            line_number=42,
        )
        assert entry.source_ref == "log:test//data/test/logs/app.log:42"


class TestOpencodeResult:
    """Tests for OpencodeResult model."""

    def test_success(self):
        result = OpencodeResult(
            success=True,
            output="Fixed the bug",
            files_changed=["api.py"],
        )
        assert result.success
        assert result.files_changed == ["api.py"]

    def test_failure(self):
        result = OpencodeResult(
            success=False,
            error="Timeout",
        )
        assert not result.success
        assert result.error == "Timeout"


class TestTestRunResult:
    """Tests for TestRunResult model."""

    def test_passed(self):
        result = TestRunResult(
            passed=True,
            total=10,
            failures=0,
            errors=0,
            skipped=1,
        )
        assert result.passed
        assert result.total == 10

    def test_failed(self):
        result = TestRunResult(
            passed=False,
            total=10,
            failures=2,
            errors=1,
        )
        assert not result.passed


class TestDevAgentObservation:
    """Tests for DevAgentObservation model."""

    def test_empty_observation(self):
        obs = DevAgentObservation()
        assert obs.bugs == []
        assert obs.workspace.cloned is False

    def test_with_bugs(self, sample_bug):
        obs = DevAgentObservation(bugs=[sample_bug])
        assert len(obs.bugs) == 1
        assert obs.bugs[0].title == sample_bug.title


class TestActionType:
    """Tests for ActionType enum."""

    def test_all_actions(self):
        actions = {a.value for a in ActionType}
        assert "analyze_bug" in actions
        assert "fix_bug" in actions
        assert "run_tests" in actions
        assert "create_pr" in actions
        assert "notify_owner" in actions
        assert "clean_workspace" in actions
        assert "skip" in actions
        assert len(actions) == 7


class TestWorkspaceState:
    """Tests for WorkspaceState model."""

    def test_defaults(self):
        ws = WorkspaceState()
        assert ws.cloned is False
        assert ws.current_branch == ""
        assert ws.is_clean is True
