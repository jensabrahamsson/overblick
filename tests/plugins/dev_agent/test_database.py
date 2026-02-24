"""Tests for dev agent database layer."""

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
    """Create a test database."""
    config = DatabaseConfig(sqlite_path=str(tmp_path / "test.db"))
    backend = SQLiteBackend(config)
    dev_db = DevAgentDB(backend)
    await dev_db.setup()
    yield dev_db
    await dev_db.close()


@pytest.mark.asyncio
async def test_upsert_and_get_bug(db):
    bug = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="Test bug",
        description="A test bug",
        priority=70,
    )
    bug_id = await db.upsert_bug(bug)
    assert bug_id > 0

    fetched = await db.get_bug(bug_id)
    assert fetched is not None
    assert fetched.title == "Test bug"
    assert fetched.source == BugSource.GITHUB_ISSUE
    assert fetched.priority == 70


@pytest.mark.asyncio
async def test_upsert_bug_deduplicates(db):
    bug1 = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="Original title",
    )
    id1 = await db.upsert_bug(bug1)

    bug2 = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#42",
        title="Updated title",
    )
    await db.upsert_bug(bug2)

    fetched = await db.get_bug(id1)
    assert fetched.title == "Updated title"


@pytest.mark.asyncio
async def test_get_bug_by_ref(db):
    bug = BugReport(
        source=BugSource.LOG_ERROR,
        source_ref="log:anomal/agent.log:100",
        title="Connection error",
    )
    await db.upsert_bug(bug)

    fetched = await db.get_bug_by_ref("log_error", "log:anomal/agent.log:100")
    assert fetched is not None
    assert fetched.title == "Connection error"


@pytest.mark.asyncio
async def test_get_active_bugs(db):
    # Insert bugs with various statuses
    for title, status in [
        ("Active 1", BugStatus.NEW),
        ("Active 2", BugStatus.FIXING),
        ("Fixed", BugStatus.FIXED),
        ("Skipped", BugStatus.SKIPPED),
    ]:
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            source_ref=f"ref-{title}",
            title=title,
            status=status,
        )
        await db.upsert_bug(bug)

    active = await db.get_active_bugs()
    # FIXED and SKIPPED should be excluded
    assert len(active) == 2
    titles = {b.title for b in active}
    assert "Active 1" in titles
    assert "Active 2" in titles


@pytest.mark.asyncio
async def test_update_bug_status(db):
    bug = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#1",
        title="Test",
    )
    bug_id = await db.upsert_bug(bug)

    await db.update_bug_status(
        bug_id, BugStatus.FIXING.value,
        branch_name="fix/1-test",
    )

    fetched = await db.get_bug(bug_id)
    assert fetched.status == BugStatus.FIXING
    assert fetched.branch_name == "fix/1-test"


@pytest.mark.asyncio
async def test_record_and_get_fix_attempts(db):
    bug = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#1",
        title="Test",
    )
    bug_id = await db.upsert_bug(bug)

    attempt = FixAttempt(
        bug_id=bug_id,
        attempt_number=1,
        files_changed=["api.py", "test_api.py"],
        tests_passed=True,
        branch_name="fix/1-test",
    )
    attempt_id = await db.record_fix_attempt(attempt)
    assert attempt_id > 0

    attempts = await db.get_fix_attempts(bug_id)
    assert len(attempts) == 1
    assert attempts[0].files_changed == ["api.py", "test_api.py"]
    assert attempts[0].tests_passed is True


@pytest.mark.asyncio
async def test_log_scan_state(db):
    # Initial offset is 0
    offset = await db.get_log_offset("/data/test/app.log")
    assert offset == 0

    # Update offset
    await db.update_log_offset("/data/test/app.log", 12345)
    offset = await db.get_log_offset("/data/test/app.log")
    assert offset == 12345

    # Update again
    await db.update_log_offset("/data/test/app.log", 99999)
    offset = await db.get_log_offset("/data/test/app.log")
    assert offset == 99999


@pytest.mark.asyncio
async def test_get_stats(db):
    # Insert some data
    for i in range(3):
        bug = BugReport(
            source=BugSource.GITHUB_ISSUE,
            source_ref=f"issue#{i}",
            title=f"Bug {i}",
            status=BugStatus.FIXED if i == 0 else BugStatus.NEW,
        )
        await db.upsert_bug(bug)

    stats = await db.get_stats()
    assert stats["total_bugs"] == 3
    assert stats["bugs_fixed"] == 1


@pytest.mark.asyncio
async def test_agentic_db_accessible(db):
    """Verify agentic DB layer is accessible."""
    assert db.agentic is not None
    # Should be able to query tick count (starts at 0)
    count = await db.agentic.get_tick_count()
    assert count == 0


@pytest.mark.asyncio
async def test_get_bugs_by_status(db):
    bug = BugReport(
        source=BugSource.GITHUB_ISSUE,
        source_ref="issue#1",
        title="Test",
        status=BugStatus.PR_CREATED,
        pr_url="https://github.com/test/pr/1",
    )
    await db.upsert_bug(bug)

    pr_bugs = await db.get_bugs_by_status(BugStatus.PR_CREATED.value)
    assert len(pr_bugs) == 1
    assert pr_bugs[0].pr_url == "https://github.com/test/pr/1"
