"""Tests for bug observer."""

from unittest.mock import AsyncMock

import pytest

from overblick.plugins.dev_agent.log_watcher import LogWatcher
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    DevAgentObservation,
    WorkspaceState,
)
from overblick.plugins.dev_agent.observation import BugObserver


@pytest.fixture
def observer(mock_db, tmp_path):
    """Create a BugObserver with mocked dependencies."""
    log_watcher = LogWatcher(
        base_log_dir=tmp_path,
        scan_identities=[],
        enabled=False,
    )
    return BugObserver(
        db=mock_db,
        log_watcher=log_watcher,
    )


class TestEnqueueIPC:
    def test_enqueue_bug_report(self, observer):
        observer.enqueue_ipc_message(
            "bug_report",
            {
                "title": "Test bug",
                "ref": "issue#1",
            },
        )
        assert len(observer._ipc_queue) == 1

    def test_queue_limit(self, observer):
        for i in range(150):
            observer.enqueue_ipc_message("bug_report", {"title": f"Bug {i}"})
        # Queue is limited to 100
        assert len(observer._ipc_queue) == 100


class TestObserve:
    @pytest.mark.asyncio
    async def test_empty_observation(self, observer, mock_db):
        obs = await observer.observe()
        assert isinstance(obs, DevAgentObservation)
        assert obs.bugs == []
        assert obs.ipc_messages_received == 0

    @pytest.mark.asyncio
    async def test_processes_ipc_messages(self, observer, mock_db):
        observer.enqueue_ipc_message(
            "bug_report",
            {
                "title": "IPC bug",
                "ref": "issue#99",
            },
        )

        obs = await observer.observe()
        assert obs.ipc_messages_received == 1
        # Should have called upsert_bug
        mock_db.upsert_bug.assert_called()

    @pytest.mark.asyncio
    async def test_skips_duplicate_ipc(self, observer, mock_db):
        """IPC bugs that already exist in DB should be skipped."""
        existing = BugReport(
            id=5,
            source=BugSource.IPC_REPORT,
            source_ref="issue#99",
            title="Already tracked",
        )
        mock_db.get_bug_by_ref = AsyncMock(return_value=existing)

        observer.enqueue_ipc_message(
            "bug_report",
            {
                "title": "IPC bug",
                "ref": "issue#99",
            },
        )

        await observer.observe()
        # upsert_bug should NOT have been called for duplicates
        mock_db.upsert_bug.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_active_bugs_from_db(self, observer, mock_db):
        active_bug = BugReport(
            id=1,
            source=BugSource.GITHUB_ISSUE,
            source_ref="issue#1",
            title="Active bug",
        )
        mock_db.get_active_bugs = AsyncMock(return_value=[active_bug])

        obs = await observer.observe()
        assert len(obs.bugs) == 1
        assert obs.bugs[0].title == "Active bug"

    @pytest.mark.asyncio
    async def test_includes_workspace_state(self, mock_db, tmp_path):
        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=[],
            enabled=False,
        )

        async def get_ws_state():
            return WorkspaceState(cloned=True, current_branch="fix/1-test")

        obs_instance = BugObserver(
            db=mock_db,
            log_watcher=log_watcher,
            workspace_state_fn=get_ws_state,
        )

        obs = await obs_instance.observe()
        assert obs.workspace.cloned is True
        assert obs.workspace.current_branch == "fix/1-test"


class TestFormatForPlanner:
    def test_empty_observation(self, observer):
        obs = DevAgentObservation()
        text = observer.format_for_planner(obs)
        assert "No active bugs" in text

    def test_with_bugs(self, observer, sample_bug):
        obs = DevAgentObservation(bugs=[sample_bug])
        text = observer.format_for_planner(obs)
        assert "Active Bugs" in text
        assert sample_bug.title in text

    def test_none_observation(self, observer):
        text = observer.format_for_planner(None)
        assert "No observations" in text

    def test_with_pending_prs(self, observer):
        obs = DevAgentObservation(
            pending_prs=["https://github.com/test/pr/1"],
        )
        text = observer.format_for_planner(obs)
        assert "Pending PRs" in text


class TestObserveWithLogScanning:
    @pytest.mark.asyncio
    async def test_observe_scans_logs_when_enabled(self, mock_db, tmp_path):
        """When log watcher is enabled, _scan_logs is called during observe."""
        # Create log files
        identity_dir = tmp_path / "anomal" / "logs"
        identity_dir.mkdir(parents=True)
        log_file = identity_dir / "agent.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Connection refused\n")

        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=["anomal"],
            enabled=True,
        )
        observer = BugObserver(db=mock_db, log_watcher=log_watcher)

        obs = await observer.observe()
        assert obs.log_errors_found >= 1

    @pytest.mark.asyncio
    async def test_observe_log_scan_with_existing_bug(self, mock_db, tmp_path):
        """Log errors that already exist in DB are not duplicated."""
        identity_dir = tmp_path / "anomal" / "logs"
        identity_dir.mkdir(parents=True)
        log_file = identity_dir / "agent.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Connection refused\n")

        mock_db.get_bug_by_ref = AsyncMock(
            return_value=BugReport(
                source=BugSource.LOG_ERROR,
                source_ref="existing",
                title="Already tracked",
            )
        )

        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=["anomal"],
            enabled=True,
        )
        observer = BugObserver(db=mock_db, log_watcher=log_watcher)

        obs = await observer.observe()
        # Errors found but not added as new bugs
        assert obs.log_errors_found >= 1
        mock_db.upsert_bug.assert_not_called()

    @pytest.mark.asyncio
    async def test_observe_log_scan_critical_error(self, mock_db, tmp_path):
        """CRITICAL errors get priority 70."""
        identity_dir = tmp_path / "anomal" / "logs"
        identity_dir.mkdir(parents=True)
        log_file = identity_dir / "agent.log"
        log_file.write_text("2026-02-23 10:00:00 CRITICAL Out of memory\n")

        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=["anomal"],
            enabled=True,
        )
        observer = BugObserver(db=mock_db, log_watcher=log_watcher)

        obs = await observer.observe()
        assert obs.log_errors_found >= 1
        # Check the bug was upserted with priority 70
        if mock_db.upsert_bug.called:
            bug_arg = mock_db.upsert_bug.call_args[0][0]
            assert bug_arg.priority == 70

    @pytest.mark.asyncio
    async def test_observe_log_scan_offset_updated(self, mock_db, tmp_path):
        """Log offset is updated when new data is read."""
        identity_dir = tmp_path / "anomal" / "logs"
        identity_dir.mkdir(parents=True)
        log_file = identity_dir / "agent.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Test error\n")

        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=["anomal"],
            enabled=True,
        )
        observer = BugObserver(db=mock_db, log_watcher=log_watcher)

        await observer.observe()
        mock_db.update_log_offset.assert_awaited()


class TestObserveWorkspaceStateException:
    @pytest.mark.asyncio
    async def test_workspace_state_fn_exception(self, mock_db, tmp_path):
        """When workspace_state_fn raises, default WorkspaceState is used."""
        log_watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=[],
            enabled=False,
        )

        async def broken_ws_fn():
            raise RuntimeError("Workspace check failed")

        observer = BugObserver(
            db=mock_db,
            log_watcher=log_watcher,
            workspace_state_fn=broken_ws_fn,
        )

        obs = await observer.observe()
        # Should get default WorkspaceState (cloned=False)
        assert obs.workspace.cloned is False


class TestFormatForPlannerExtended:
    def test_format_with_error_text_and_analysis(self, observer):
        """Bug with error_text and analysis are included in format."""
        bug = BugReport(
            id=1,
            source=BugSource.GITHUB_ISSUE,
            source_ref="issue#1",
            title="Test bug",
            error_text="TypeError: NoneType",
            analysis="Missing null check in api.py",
        )
        obs = DevAgentObservation(bugs=[bug])
        text = observer.format_for_planner(obs)
        assert "TypeError: NoneType" in text
        assert "Missing null check" in text

    def test_format_with_recent_fixes(self, observer):
        """Recent fix attempts are included in format."""
        from overblick.plugins.dev_agent.models import FixAttempt

        attempt = FixAttempt(
            bug_id=1,
            attempt_number=1,
            tests_passed=True,
            committed=True,
        )
        obs = DevAgentObservation(recent_fixes=[attempt])
        text = observer.format_for_planner(obs)
        assert "Recent Fix Attempts" in text
        assert "PASS" in text

    def test_format_with_failed_test(self, observer):
        """Failed test attempt shows FAIL."""
        from overblick.plugins.dev_agent.models import FixAttempt

        attempt = FixAttempt(
            bug_id=2,
            attempt_number=1,
            tests_passed=False,
            committed=False,
        )
        obs = DevAgentObservation(recent_fixes=[attempt])
        text = observer.format_for_planner(obs)
        assert "FAIL" in text

    def test_format_with_log_errors(self, observer):
        """Log errors count is shown."""
        obs = DevAgentObservation(log_errors_found=5)
        text = observer.format_for_planner(obs)
        assert "5 new errors found" in text

    def test_format_with_ipc_messages(self, observer):
        """IPC message count is shown."""
        obs = DevAgentObservation(ipc_messages_received=3)
        text = observer.format_for_planner(obs)
        assert "3 messages received" in text


class TestIPCToBug:
    def test_bug_report(self):
        msg = {
            "type": "bug_report",
            "payload": {
                "title": "Test",
                "ref": "issue#1",
                "description": "A bug",
                "priority": 80,
            },
        }
        bug = BugObserver._ipc_to_bug(msg)
        assert bug is not None
        assert bug.title == "Test"
        assert bug.source == BugSource.IPC_REPORT
        assert bug.priority == 80

    def test_log_alert(self):
        msg = {
            "type": "log_alert",
            "payload": {
                "message": "DB error",
                "ref": "log:anomal/agent.log:42",
                "identity": "anomal",
            },
        }
        bug = BugObserver._ipc_to_bug(msg)
        assert bug is not None
        assert bug.title == "DB error"
        assert bug.source == BugSource.LOG_ERROR

    def test_unknown_type(self):
        msg = {"type": "unknown", "payload": {}}
        bug = BugObserver._ipc_to_bug(msg)
        assert bug is None
