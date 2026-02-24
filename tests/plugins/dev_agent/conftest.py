"""
Shared fixtures for dev agent plugin tests.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    DevAgentObservation,
    FixAttempt,
    LogErrorEntry,
    WorkspaceState,
)


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def workspace_path(tmp_path):
    """Provide a workspace directory."""
    ws = tmp_path / "workspace" / "overblick"
    ws.mkdir(parents=True)
    return ws


@pytest.fixture
def sample_bug():
    """A sample bug report for testing."""
    return BugReport(
        id=1,
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="API returns 500 on empty input",
        description="The /api/process endpoint returns a 500 error when called with empty JSON body",
        error_text="TypeError: 'NoneType' object is not subscriptable\n  File api.py, line 42",
        file_path="overblick/api.py",
        status=BugStatus.NEW,
        priority=70,
        fix_attempts=0,
        max_attempts=3,
    )


@pytest.fixture
def sample_bug_failed():
    """A bug that has exhausted all fix attempts."""
    return BugReport(
        id=2,
        source=BugSource.LOG_ERROR,
        source_ref="log:anomal/agent.log:100",
        title="Database connection timeout",
        description="SQLite connection pool exhausted",
        status=BugStatus.FAILED,
        priority=60,
        fix_attempts=3,
        max_attempts=3,
    )


@pytest.fixture
def sample_fix_attempt():
    """A sample fix attempt."""
    return FixAttempt(
        id=1,
        bug_id=1,
        attempt_number=1,
        analysis="Root cause: missing None check in api.py",
        files_changed=["overblick/api.py", "tests/test_api.py"],
        tests_passed=True,
        test_output="5 passed in 1.23s",
        opencode_output="Fixed by adding None check",
        committed=True,
        branch_name="fix/1-api-returns-500",
        duration_seconds=45.0,
    )


@pytest.fixture
def sample_observation(sample_bug):
    """A sample observation with one bug."""
    return DevAgentObservation(
        bugs=[sample_bug],
        workspace=WorkspaceState(
            cloned=True,
            current_branch="main",
            is_clean=True,
            repo_url="https://github.com/test/repo.git",
            workspace_path="/tmp/workspace",
        ),
        log_errors_found=0,
        ipc_messages_received=0,
    )


@pytest.fixture
def sample_log_error():
    """A sample log error entry."""
    return LogErrorEntry(
        file_path="/data/anomal/logs/agent.log",
        line_number=142,
        identity="anomal",
        level="ERROR",
        message="Connection refused: localhost:27017",
        traceback=(
            "Traceback (most recent call last):\n"
            "  File agent.py, line 142\n"
            "ConnectionRefusedError: [Errno 111] Connection refused"
        ),
        timestamp="2026-02-23 10:30:00",
    )


@pytest.fixture
def mock_db():
    """A mock DevAgentDB."""
    db = AsyncMock()
    db.get_active_bugs = AsyncMock(return_value=[])
    db.get_bug = AsyncMock(return_value=None)
    db.get_bug_by_ref = AsyncMock(return_value=None)
    db.upsert_bug = AsyncMock(return_value=1)
    db.update_bug_status = AsyncMock()
    db.record_fix_attempt = AsyncMock(return_value=1)
    db.get_fix_attempts = AsyncMock(return_value=[])
    db.get_recent_attempts = AsyncMock(return_value=[])
    db.get_bugs_by_status = AsyncMock(return_value=[])
    db.get_log_offset = AsyncMock(return_value=0)
    db.update_log_offset = AsyncMock()
    return db
