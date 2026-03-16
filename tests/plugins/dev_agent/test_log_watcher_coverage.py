"""
Additional coverage tests for log_watcher module.

Covers uncovered lines:
- 87-88: OSError when stat() fails
- 103-105: OSError when reading file
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from overblick.plugins.dev_agent.log_watcher import LogWatcher


class TestEnabledProperty:
    def test_should_return_enabled_state(self, tmp_path):
        """Line 47: enabled property."""
        watcher = LogWatcher(base_log_dir=tmp_path, scan_identities=[], enabled=True)
        assert watcher.enabled is True

        disabled = LogWatcher(base_log_dir=tmp_path, scan_identities=[], enabled=False)
        assert disabled.enabled is False


class TestScanFileOSErrors:
    def test_should_handle_stat_oserror(self, tmp_path):
        """Lines 87-88: OSError on stat() returns empty."""
        watcher = LogWatcher(base_log_dir=tmp_path, scan_identities=["a"], enabled=True)
        log_file = tmp_path / "test.log"
        log_file.write_text("test")

        original_stat = log_file.stat

        def stat_fail():
            raise OSError("Permission denied")

        with patch.object(type(log_file), "stat", new=property(lambda self: stat_fail())):
            # Use a different approach: mock the file's stat method
            pass

        # Alternative: use a real file and mock stat on the specific instance
        with patch.object(Path, "stat", side_effect=OSError("Permission denied")):
            # is_file also uses stat internally - we need is_file to return True first
            with patch.object(Path, "is_file", return_value=True):
                errors, offset = watcher.scan_file(log_file, "test", 0)
                assert errors == []
                assert offset == 0

    def test_should_handle_read_oserror(self, tmp_path):
        """Lines 103-105: OSError when reading file returns empty."""
        watcher = LogWatcher(base_log_dir=tmp_path, scan_identities=["a"], enabled=True)
        log_file = tmp_path / "test.log"
        log_file.write_text("2026-02-23 10:00:00 ERROR Test\n")

        with patch("builtins.open", side_effect=OSError("IO error")):
            errors, offset = watcher.scan_file(log_file, "test", 0)
            assert errors == []
            assert offset == 0


class TestGetLogFilesAlternativePath:
    def test_should_find_alternative_data_path(self, tmp_path):
        """Lines 60-61: falls back to data/<identity>/logs/ path."""
        # Create the alternative path structure
        alt_dir = tmp_path / "data" / "anomal" / "logs"
        alt_dir.mkdir(parents=True)
        (alt_dir / "agent.log").write_text("test")

        # Base dir doesn't have the identity subdir
        base_dir = tmp_path / "config"
        base_dir.mkdir()

        watcher = LogWatcher(
            base_log_dir=base_dir,
            scan_identities=["anomal"],
        )
        files = watcher.get_log_files()
        # The alternative path check won't work because base_dir.parent / "data"
        # would need to resolve to tmp_path / "data"
        # Actually: base_dir = tmp_path / "config", base_dir.parent = tmp_path
        # So base_dir.parent / "data" / "anomal" / "logs" = tmp_path / "data" / "anomal" / "logs"
        assert len(files) == 1
        assert files[0][0] == "anomal"
