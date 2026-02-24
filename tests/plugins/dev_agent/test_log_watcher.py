"""Tests for log watcher."""

import pytest

from overblick.plugins.dev_agent.log_watcher import LogWatcher


@pytest.fixture
def watcher(tmp_path):
    """Create a log watcher with test directories."""
    # Create identity log dirs
    for identity in ["anomal", "cherry"]:
        log_dir = tmp_path / identity / "logs"
        log_dir.mkdir(parents=True)

    return LogWatcher(
        base_log_dir=tmp_path,
        scan_identities=["anomal", "cherry"],
        enabled=True,
    )


@pytest.fixture
def disabled_watcher(tmp_path):
    return LogWatcher(
        base_log_dir=tmp_path,
        scan_identities=["anomal"],
        enabled=False,
    )


class TestGetLogFiles:
    def test_finds_log_files(self, watcher, tmp_path):
        # Create log files
        (tmp_path / "anomal" / "logs" / "agent.log").write_text("test")
        (tmp_path / "cherry" / "logs" / "agent.log").write_text("test")

        files = watcher.get_log_files()
        assert len(files) == 2
        identities = {f[0] for f in files}
        assert "anomal" in identities
        assert "cherry" in identities

    def test_no_log_files(self, watcher):
        files = watcher.get_log_files()
        assert len(files) == 0

    def test_skips_missing_identities(self, tmp_path):
        watcher = LogWatcher(
            base_log_dir=tmp_path,
            scan_identities=["nonexistent"],
        )
        files = watcher.get_log_files()
        assert len(files) == 0


class TestScanFile:
    def test_scan_error_line(self, watcher, tmp_path):
        log_file = tmp_path / "anomal" / "logs" / "agent.log"
        log_file.write_text(
            "2026-02-23 10:00:00 INFO  Normal log line\n"
            "2026-02-23 10:00:01 ERROR Connection refused: localhost:27017\n"
            "2026-02-23 10:00:02 INFO  Recovered\n"
        )

        errors, new_offset = watcher.scan_file(log_file, "anomal", 0)
        assert len(errors) == 1
        assert "Connection refused" in errors[0].message
        assert errors[0].identity == "anomal"
        assert errors[0].level == "ERROR"
        assert new_offset > 0

    def test_scan_with_traceback(self, watcher, tmp_path):
        log_file = tmp_path / "anomal" / "logs" / "agent.log"
        log_file.write_text(
            "2026-02-23 10:00:00 ERROR Something failed\n"
            "Traceback (most recent call last):\n"
            "  File \"agent.py\", line 42\n"
            "  File \"db.py\", line 10\n"
            "ConnectionError: Connection refused\n"
            "2026-02-23 10:00:02 INFO  Next line\n"
        )

        errors, new_offset = watcher.scan_file(log_file, "anomal", 0)
        assert len(errors) == 1
        assert "Traceback" in errors[0].traceback
        assert "ConnectionError" in errors[0].traceback

    def test_scan_critical(self, watcher, tmp_path):
        log_file = tmp_path / "anomal" / "logs" / "agent.log"
        log_file.write_text(
            "2026-02-23 10:00:00 CRITICAL Out of memory\n"
        )

        errors, _ = watcher.scan_file(log_file, "anomal", 0)
        assert len(errors) == 1
        assert errors[0].level == "CRITICAL"

    def test_scan_from_offset(self, watcher, tmp_path):
        log_file = tmp_path / "anomal" / "logs" / "agent.log"
        content = (
            "2026-02-23 10:00:00 ERROR First error\n"
            "2026-02-23 10:00:01 ERROR Second error\n"
        )
        log_file.write_text(content)

        # Scan from start to get offset
        errors1, offset1 = watcher.scan_file(log_file, "anomal", 0)
        assert len(errors1) == 2

        # Scan from previous offset — no new content
        errors2, offset2 = watcher.scan_file(log_file, "anomal", offset1)
        assert len(errors2) == 0
        assert offset2 == offset1

    def test_scan_rotated_file(self, watcher, tmp_path):
        """Test that file rotation (smaller file) resets offset."""
        log_file = tmp_path / "anomal" / "logs" / "agent.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Old error\n")

        _, offset1 = watcher.scan_file(log_file, "anomal", 0)

        # Simulate rotation — file is now smaller
        log_file.write_text("2026-02-23 11:00:00 ERROR New error\n")

        errors, _ = watcher.scan_file(log_file, "anomal", offset1 + 1000)
        assert len(errors) == 1
        assert "New error" in errors[0].message

    def test_scan_disabled(self, disabled_watcher, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Test\n")
        errors, offset = disabled_watcher.scan_file(log_file, "test", 0)
        assert errors == []
        assert offset == 0

    def test_scan_missing_file(self, watcher):
        from pathlib import Path
        errors, offset = watcher.scan_file(Path("/nonexistent.log"), "test", 0)
        assert errors == []
        assert offset == 0


class TestDeduplicateErrors:
    def test_deduplicates_same_message(self):
        from overblick.plugins.dev_agent.models import LogErrorEntry

        errors = [
            LogErrorEntry(file_path="a.log", message="Connection refused", identity="a"),
            LogErrorEntry(file_path="a.log", message="Connection refused", identity="a"),
            LogErrorEntry(file_path="a.log", message="Different error", identity="a"),
        ]
        unique = LogWatcher.deduplicate_errors(errors)
        assert len(unique) == 2

    def test_different_tracebacks_not_deduped(self):
        from overblick.plugins.dev_agent.models import LogErrorEntry

        errors = [
            LogErrorEntry(
                file_path="a.log", message="Error",
                traceback="Traceback\nTypeError: NoneType",
            ),
            LogErrorEntry(
                file_path="a.log", message="Error",
                traceback="Traceback\nValueError: invalid",
            ),
        ]
        unique = LogWatcher.deduplicate_errors(errors)
        assert len(unique) == 2

    def test_empty_list(self):
        assert LogWatcher.deduplicate_errors([]) == []
