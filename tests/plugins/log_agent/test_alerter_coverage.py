"""
Additional coverage tests for alerter module.

Covers uncovered line:
- 47: format_scan_summary with errors_found > 0 but no entries
"""

from overblick.plugins.log_agent.alerter import AlertFormatter
from overblick.plugins.log_agent.models import LogEntry, LogScanResult


class TestFormatScanSummaryErrorsNoEntries:
    def test_should_include_error_count_even_without_entries(self):
        """Line 47: results with errors_found > 0 but empty entries list."""
        results = [
            LogScanResult(
                identity="anomal",
                errors_found=3,
                criticals_found=0,
                entries=[],  # No entries but errors_found > 0
            ),
        ]
        text = AlertFormatter.format_scan_summary(results)

        assert text is not None
        assert "ERROR: 3 error entries found" in text
        # Should skip identity section since no entries
        assert "anomal:" not in text
