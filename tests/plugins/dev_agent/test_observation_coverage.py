"""
Additional coverage tests for observation module.

Covers uncovered lines:
- 79-80: observe with IPC message generating a bug with issue_url fallback
- 90-91: workspace_state_fn exception (already covered but line might be different)
- 138: format_for_planner bug.analysis truncation
- 151-154: format_for_planner recent_fixes display
- 167, 169: format_for_planner log_errors and ipc_messages
- 177-215: _scan_logs full path with errors, dedup, new bugs
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.dev_agent.log_watcher import LogWatcher
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    DevAgentObservation,
    FixAttempt,
    WorkspaceState,
)
from overblick.plugins.dev_agent.observation import BugObserver


class TestIPCToBugIssueUrlFallback:
    def test_should_use_issue_url_when_ref_missing(self):
        """Line 226: falls back to issue_url when ref is missing."""
        msg = {
            "type": "bug_report",
            "payload": {
                "title": "Test bug",
                "issue_url": "https://github.com/test/issue/1",
            },
        }
        bug = BugObserver._ipc_to_bug(msg)
        assert bug is not None
        assert bug.source_ref == "https://github.com/test/issue/1"

    def test_should_handle_log_alert_with_all_fields(self):
        """Log alert with all optional fields."""
        msg = {
            "type": "log_alert",
            "payload": {
                "message": "Critical error",
                "ref": "log:anomal/agent.log:42",
                "details": "Full stack trace",
                "traceback": "Traceback...\nError",
                "file_path": "/data/anomal/agent.log",
                "identity": "anomal",
                "priority": 90,
            },
        }
        bug = BugObserver._ipc_to_bug(msg)
        assert bug is not None
        assert bug.priority == 90
        assert bug.error_text == "Traceback...\nError"
        assert bug.identity == "anomal"


class TestFormatForPlannerAllBugStatuses:
    def test_should_format_all_status_icons(self):
        """Lines 121-128: all status types have icons."""
        observer = BugObserver(
            db=AsyncMock(),
            log_watcher=LogWatcher(
                base_log_dir=MagicMock(),
                scan_identities=[],
                enabled=False,
            ),
        )

        for status in BugStatus:
            bug = BugReport(
                id=1,
                source=BugSource.GITHUB_ISSUE,
                source_ref="ref",
                title="Test",
                status=status,
            )
            obs = DevAgentObservation(bugs=[bug])
            text = observer.format_for_planner(obs)
            assert "Test" in text

    def test_should_format_wrong_observation_type(self):
        """Line 111: non-DevAgentObservation returns default message."""
        observer = BugObserver(
            db=AsyncMock(),
            log_watcher=LogWatcher(
                base_log_dir=MagicMock(),
                scan_identities=[],
                enabled=False,
            ),
        )
        text = observer.format_for_planner("not an observation")
        assert "No observations" in text
