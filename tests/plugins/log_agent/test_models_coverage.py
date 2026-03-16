"""
Coverage tests for log_agent models module.

Covers uncovered line:
- 56: LogEntry.source_ref property
"""

from overblick.plugins.log_agent.models import LogEntry


class TestLogEntrySourceRef:
    def test_should_generate_source_ref(self):
        """Line 56: source_ref property generates correct format."""
        entry = LogEntry(
            identity="anomal",
            file_path="/data/anomal/agent.log",
            line_number=42,
            level="ERROR",
            message="Connection refused",
        )
        ref = entry.source_ref
        assert ref == "log:anomal//data/anomal/agent.log:42"
