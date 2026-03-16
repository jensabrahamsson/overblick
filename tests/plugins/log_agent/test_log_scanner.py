"""
Tests for the log scanner — file scanning, offset management, deduplication.
"""

from pathlib import Path

import pytest

from overblick.plugins.log_agent.log_scanner import LogScanner
from overblick.plugins.log_agent.models import LogEntry


class TestScanFile:
    """Tests for LogScanner.scan_file()."""

    def test_detects_error_lines(self, sample_log_dir):
        """ERROR lines are detected in log files."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"
        entries, offset = scanner.scan_file(log_file, "anomal")

        error_entries = [e for e in entries if e.level == "ERROR"]
        assert len(error_entries) >= 1
        assert "timeout" in error_entries[0].message.lower()

    def test_detects_critical_lines(self, sample_log_dir):
        """CRITICAL lines are detected."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"
        entries, offset = scanner.scan_file(log_file, "anomal")

        critical_entries = [e for e in entries if e.level == "CRITICAL"]
        assert len(critical_entries) == 1
        assert "preflight" in critical_entries[0].message.lower()

    def test_captures_traceback(self, sample_log_dir):
        """Traceback lines following an ERROR are captured."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"
        entries, _ = scanner.scan_file(log_file, "anomal")

        error_with_tb = [e for e in entries if e.traceback]
        assert len(error_with_tb) >= 1
        assert "TimeoutError" in error_with_tb[0].traceback

    def test_ignores_info_lines(self, sample_log_dir):
        """INFO lines are not included in results."""
        scanner = LogScanner(sample_log_dir, identities=["cherry"])
        log_file = sample_log_dir / "cherry" / "cherry.log"
        entries, _ = scanner.scan_file(log_file, "cherry")

        assert len(entries) == 0

    def test_returns_new_offset(self, sample_log_dir):
        """Returns byte offset past the data read."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"
        _, offset = scanner.scan_file(log_file, "anomal")

        assert offset > 0
        file_size = log_file.stat().st_size
        assert offset == file_size

    def test_incremental_scan_skips_old_data(self, sample_log_dir):
        """Second scan from offset finds no new entries."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"

        entries1, offset1 = scanner.scan_file(log_file, "anomal")
        scanner.set_offset(str(log_file), offset1)

        entries2, offset2 = scanner.scan_file(log_file, "anomal")
        assert len(entries2) == 0
        assert offset2 == offset1

    def test_file_rotation_resets_offset(self, sample_log_dir):
        """When file shrinks (rotation), offset resets to 0."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        log_file = sample_log_dir / "anomal" / "anomal.log"

        # Scan once to get offset
        _, offset = scanner.scan_file(log_file, "anomal")
        scanner.set_offset(str(log_file), offset)

        # Simulate rotation: write smaller content
        log_file.write_text("2026-02-26 04:00:00,000 - core - ERROR - New error after rotation\n")

        entries, new_offset = scanner.scan_file(log_file, "anomal")
        assert len(entries) == 1
        assert "rotation" in entries[0].message.lower()

    def test_nonexistent_file_returns_empty(self, tmp_path):
        """Nonexistent file returns empty list."""
        scanner = LogScanner(tmp_path, identities=["missing"])
        fake_file = tmp_path / "missing" / "missing.log"
        entries, offset = scanner.scan_file(fake_file, "missing")

        assert entries == []


class TestScanIdentity:
    """Tests for LogScanner.scan_identity()."""

    def test_scans_all_files_for_identity(self, sample_log_dir):
        """scan_identity() scans all .log files for the identity."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        result = scanner.scan_identity("anomal")

        assert result.identity == "anomal"
        assert result.errors_found >= 1
        assert result.criticals_found == 1
        assert result.scan_duration_ms >= 0

    def test_empty_identity_returns_zero_counts(self, sample_log_dir):
        """Identity with no errors returns zero counts."""
        scanner = LogScanner(sample_log_dir, identities=["cherry"])
        result = scanner.scan_identity("cherry")

        assert result.errors_found == 0
        assert result.criticals_found == 0
        assert len(result.entries) == 0

    def test_nonexistent_identity_returns_empty(self, sample_log_dir):
        """Nonexistent identity returns empty result."""
        scanner = LogScanner(sample_log_dir, identities=["nonexistent"])
        result = scanner.scan_identity("nonexistent")

        assert result.errors_found == 0
        assert len(result.entries) == 0


class TestScanAll:
    """Tests for LogScanner.scan_all()."""

    def test_scans_all_configured_identities(self, sample_log_dir):
        """scan_all() returns results for each identity."""
        scanner = LogScanner(
            sample_log_dir,
            identities=["anomal", "cherry", "stal"],
        )
        results = scanner.scan_all()

        assert len(results) == 3
        identities = {r.identity for r in results}
        assert identities == {"anomal", "cherry", "stal"}


class TestDeduplication:
    """Tests for LogScanner deduplication."""

    def test_duplicate_messages_removed(self, sample_log_dir):
        """Duplicate error messages from stal are deduplicated."""
        scanner = LogScanner(sample_log_dir, identities=["stal"])
        result = scanner.scan_identity("stal")

        # stal.log has 2 identical ERROR lines
        assert result.errors_found == 1  # Deduplicated to 1


class TestScanFileEdgeCases:
    """Edge cases for scan_file not covered by main tests."""

    def test_last_entry_with_traceback(self, tmp_path):
        """Last entry with traceback lines (no next log line to close it)."""
        log_dir = tmp_path / "test"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        log_file.write_text(
            "2026-02-26 03:00:00,000 - module - ERROR - Final error\n"
            "  Traceback (most recent call last):\n"
            '    File "main.py", line 10\n'
        )
        scanner = LogScanner(tmp_path, identities=["test"])
        entries, _ = scanner.scan_file(log_file, "test")
        assert len(entries) == 1
        assert entries[0].traceback
        assert "Traceback" in entries[0].traceback

    def test_max_entries_cap(self, tmp_path):
        """Scan stops after _MAX_ENTRIES_PER_SCAN entries."""
        log_dir = tmp_path / "test"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        lines = []
        for i in range(120):
            lines.append(f"2026-02-26 03:00:{i:02d},000 - module - ERROR - Error {i}")
        log_file.write_text("\n".join(lines) + "\n")

        scanner = LogScanner(tmp_path, identities=["test"])
        entries, _ = scanner.scan_file(log_file, "test")
        # Capped at 100
        assert len(entries) == 100

    def test_os_error_reading_file(self, tmp_path):
        """OSError during file read returns empty list."""
        log_dir = tmp_path / "test"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        log_file.write_text("2026-02-26 03:00:00,000 - module - ERROR - Test\n")

        scanner = LogScanner(tmp_path, identities=["test"])

        from unittest.mock import patch, mock_open

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            entries, offset = scanner.scan_file(log_file, "test")

        assert entries == []

    def test_non_error_level_resets_current_entry(self, tmp_path):
        """WARNING entries matching the pattern but not in levels are handled."""
        log_dir = tmp_path / "test"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        log_file.write_text(
            "2026-02-26 03:00:00,000 - module - ERROR - First error\n"
            "  Traceback (most recent call last):\n"
            "2026-02-26 03:00:01,000 - module - WARNING - Just a warning\n"
            "2026-02-26 03:00:02,000 - module - ERROR - Second error\n"
        )
        scanner = LogScanner(tmp_path, identities=["test"], levels=("ERROR",))
        entries, _ = scanner.scan_file(log_file, "test")
        assert len(entries) == 2


class TestProperties:
    def test_identities_property(self, sample_log_dir):
        """identities property returns a copy of configured identities."""
        scanner = LogScanner(sample_log_dir, identities=["anomal", "cherry"])
        assert scanner.identities == ["anomal", "cherry"]
        # Ensure it's a copy
        scanner.identities.append("test")
        assert scanner.identities == ["anomal", "cherry"]


class TestOffsetManagement:
    """Tests for byte offset management."""

    def test_get_set_offset(self, sample_log_dir):
        """Offsets can be set and retrieved."""
        scanner = LogScanner(sample_log_dir, identities=["anomal"])
        scanner.set_offset("/some/path.log", 42)

        assert scanner.get_offset("/some/path.log") == 42

    def test_default_offset_is_zero(self, sample_log_dir):
        """Default offset for unknown file is 0."""
        scanner = LogScanner(sample_log_dir, identities=[])
        assert scanner.get_offset("/unknown/file.log") == 0
