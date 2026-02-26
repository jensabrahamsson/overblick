"""
Tests for the alert formatter and deduplicator.
"""

import time

import pytest

from overblick.plugins.log_agent.alerter import AlertDeduplicator, AlertFormatter
from overblick.plugins.log_agent.models import AlertSeverity, LogEntry, LogScanResult


class TestAlertFormatter:
    """Tests for AlertFormatter."""

    def test_format_summary_with_errors(self):
        """Format summary includes identity and error details."""
        results = [
            LogScanResult(
                identity="anomal",
                errors_found=1,
                criticals_found=0,
                entries=[
                    LogEntry(identity="anomal", file_path="anomal.log", level="ERROR", message="LLM timeout"),
                ],
            ),
        ]
        text = AlertFormatter.format_scan_summary(results)

        assert text is not None
        assert "Vakt Log Alert" in text
        assert "anomal" in text
        assert "LLM timeout" in text

    def test_format_summary_no_errors_returns_none(self):
        """Returns None when there are no errors."""
        results = [
            LogScanResult(identity="cherry", errors_found=0, criticals_found=0, entries=[]),
        ]
        assert AlertFormatter.format_scan_summary(results) is None

    def test_format_summary_with_criticals(self):
        """Critical entries are highlighted."""
        results = [
            LogScanResult(
                identity="stal",
                errors_found=0,
                criticals_found=1,
                entries=[
                    LogEntry(identity="stal", file_path="stal.log", level="CRITICAL", message="Database corruption"),
                ],
            ),
        ]
        text = AlertFormatter.format_scan_summary(results)

        assert text is not None
        assert "CRITICAL" in text
        assert "Database corruption" in text

    def test_format_summary_truncates_long_lists(self):
        """More than 5 entries per identity are truncated."""
        entries = [
            LogEntry(identity="anomal", file_path="anomal.log", level="ERROR", message=f"Error {i}")
            for i in range(8)
        ]
        results = [
            LogScanResult(identity="anomal", errors_found=8, criticals_found=0, entries=entries),
        ]
        text = AlertFormatter.format_scan_summary(results)

        assert text is not None
        assert "...and 3 more" in text

    def test_format_critical_alert(self):
        """Format a critical alert with traceback."""
        entry = LogEntry(
            identity="anomal",
            file_path="anomal.log",
            level="CRITICAL",
            message="Unhandled exception in main loop",
            traceback="  File 'main.py', line 42\n  RuntimeError: boom",
        )
        text = AlertFormatter.format_critical_alert(entry)

        assert "CRITICAL ALERT" in text
        assert "anomal" in text
        assert "RuntimeError" in text

    def test_severity_from_results_critical(self):
        """Critical results yield CRITICAL severity."""
        results = [
            LogScanResult(identity="anomal", errors_found=1, criticals_found=1, entries=[]),
        ]
        assert AlertFormatter.severity_from_results(results) == AlertSeverity.CRITICAL

    def test_severity_from_results_error(self):
        """Error-only results yield ERROR severity."""
        results = [
            LogScanResult(identity="anomal", errors_found=3, criticals_found=0, entries=[]),
        ]
        assert AlertFormatter.severity_from_results(results) == AlertSeverity.ERROR

    def test_severity_from_results_info(self):
        """Clean results yield INFO severity."""
        results = [
            LogScanResult(identity="anomal", errors_found=0, criticals_found=0, entries=[]),
        ]
        assert AlertFormatter.severity_from_results(results) == AlertSeverity.INFO


class TestAlertDeduplicator:
    """Tests for AlertDeduplicator."""

    def test_first_alert_passes(self):
        """First alert for an entry always passes."""
        dedup = AlertDeduplicator(cooldown_seconds=3600)
        entry = LogEntry(identity="anomal", file_path="anomal.log", level="ERROR", message="Test error")

        assert dedup.should_alert(entry) is True

    def test_duplicate_within_cooldown_blocked(self):
        """Same error within cooldown is blocked."""
        dedup = AlertDeduplicator(cooldown_seconds=3600)
        entry = LogEntry(identity="anomal", file_path="anomal.log", level="ERROR", message="Test error")

        dedup.should_alert(entry)  # First — passes
        assert dedup.should_alert(entry) is False  # Second — blocked

    def test_different_errors_both_pass(self):
        """Different error messages are not deduplicated."""
        dedup = AlertDeduplicator(cooldown_seconds=3600)
        entry1 = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Error A")
        entry2 = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Error B")

        assert dedup.should_alert(entry1) is True
        assert dedup.should_alert(entry2) is True

    def test_same_error_different_identity_both_pass(self):
        """Same message from different identities are separate alerts."""
        dedup = AlertDeduplicator(cooldown_seconds=3600)
        entry1 = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Shared error")
        entry2 = LogEntry(identity="stal", file_path="b.log", level="ERROR", message="Shared error")

        assert dedup.should_alert(entry1) is True
        assert dedup.should_alert(entry2) is True

    def test_expired_cooldown_allows_realert(self):
        """After cooldown expires, the same error can be alerted again."""
        dedup = AlertDeduplicator(cooldown_seconds=1)
        entry = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Test")

        dedup.should_alert(entry)  # Mark as sent

        # Manually expire the cooldown
        key = "anomal:ERROR:Test"
        dedup._sent[key] = time.time() - 2  # 2 seconds ago (cooldown=1s)

        assert dedup.should_alert(entry) is True

    def test_cleanup_removes_expired(self):
        """cleanup() removes expired entries."""
        dedup = AlertDeduplicator(cooldown_seconds=1)
        entry = LogEntry(identity="anomal", file_path="a.log", level="ERROR", message="Old")

        dedup.should_alert(entry)
        dedup._sent["anomal:ERROR:Old"] = time.time() - 2

        removed = dedup.cleanup()
        assert removed == 1
        assert len(dedup._sent) == 0
