"""
Additional coverage tests for dev_agent database module.

Covers uncovered lines:
- 210-211: update_bug_status with fix_attempts kwarg
- 254-258: get_recent_attempts
- 360-361: _row_to_attempt with invalid JSON in files_changed
"""

import pytest

from overblick.core.database.base import DatabaseConfig
from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.plugins.dev_agent.database import DevAgentDB
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    FixAttempt,
)


@pytest.fixture
async def db(tmp_path):
    config = DatabaseConfig(sqlite_path=str(tmp_path / "test.db"))
    backend = SQLiteBackend(config)
    dev_db = DevAgentDB(backend)
    await dev_db.setup()
    yield dev_db
    await dev_db.close()


class TestUpdateBugStatusWithFixAttempts:
    @pytest.mark.asyncio
    async def test_should_update_fix_attempts(self, db):
        """Lines 210-211: fix_attempts kwarg is applied."""
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            source_ref="issue#99",
            title="Test",
        )
        bug_id = await db.upsert_bug(bug)

        await db.update_bug_status(
            bug_id,
            BugStatus.FIXING.value,
            fix_attempts="2",
        )

        fetched = await db.get_bug(bug_id)
        assert fetched.fix_attempts == 2


class TestGetRecentAttempts:
    @pytest.mark.asyncio
    async def test_should_return_recent_attempts(self, db):
        """Lines 254-258: get_recent_attempts across all bugs."""
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            source_ref="issue#1",
            title="Test",
        )
        bug_id = await db.upsert_bug(bug)

        for i in range(3):
            attempt = FixAttempt(
                bug_id=bug_id,
                attempt_number=i + 1,
                tests_passed=i == 2,
            )
            await db.record_fix_attempt(attempt)

        recent = await db.get_recent_attempts(limit=2)
        assert len(recent) == 2
        # Should be ordered by id DESC (most recent first)
        assert recent[0].attempt_number == 3

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_attempts(self, db):
        recent = await db.get_recent_attempts(limit=5)
        assert recent == []


class TestRowToAttemptInvalidJson:
    def test_should_handle_invalid_json_in_files_changed(self):
        """Lines 360-361: invalid JSON in files_changed falls back to empty list."""
        row = {
            "id": 1,
            "bug_id": 1,
            "attempt_number": 1,
            "analysis": "",
            "files_changed": "not valid json",
            "tests_passed": 0,
            "test_output": "",
            "opencode_output": "",
            "committed": 0,
            "branch_name": "",
            "duration_seconds": 0.0,
            "created_at": "",
        }
        attempt = DevAgentDB._row_to_attempt(row)
        assert attempt.files_changed == []

    def test_should_handle_none_files_changed(self):
        """files_changed is None."""
        row = {
            "id": 1,
            "bug_id": 1,
            "attempt_number": 1,
            "files_changed": None,
            "tests_passed": 1,
            "test_output": "",
            "opencode_output": "",
            "committed": 1,
            "branch_name": "",
            "duration_seconds": 0.0,
            "created_at": "",
        }
        attempt = DevAgentDB._row_to_attempt(row)
        assert attempt.files_changed == []
