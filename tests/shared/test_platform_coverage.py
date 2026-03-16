"""Additional tests for platform.py — cover lines 81, 84, 97, 105-107, 128, 131-146, 167, 189."""

import asyncio
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import overblick.shared.platform as plat
from overblick.shared.platform import (
    enforce_restrictive_permissions,
    get_python_executable,
    verify_restrictive_permissions,
)


unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix-only"
)


class TestVerifyRestrictivePermissions:
    def test_windows_always_true(self):
        """Cover line 81: Windows returns True."""
        with patch.object(plat, "IS_WINDOWS", True):
            assert plat.verify_restrictive_permissions(Path("/any")) is True

    def test_nonexistent_path_returns_true(self, tmp_path):
        """Cover line 84: non-existent path returns True."""
        p = tmp_path / "nonexistent"
        assert verify_restrictive_permissions(p) is True

    @unix_only
    def test_directory_not_owner_only(self, tmp_path):
        """Cover line 97: directory with non-owner-only but no group write."""
        d = tmp_path / "dir"
        d.mkdir()
        os.chmod(str(d), 0o755)  # group/other have read+execute
        result = verify_restrictive_permissions(d, require_owner_only=False, is_directory=True)
        assert result is True

    @unix_only
    def test_directory_with_group_write(self, tmp_path):
        """Cover line 97: directory with group write returns False."""
        d = tmp_path / "dir"
        d.mkdir()
        os.chmod(str(d), 0o770)
        result = verify_restrictive_permissions(d, require_owner_only=False, is_directory=True)
        assert result is False

    @unix_only
    def test_file_not_owner_only(self, tmp_path):
        """Cover lines 105: file with relaxed permissions check."""
        f = tmp_path / "file"
        f.write_text("test")
        os.chmod(str(f), 0o644)  # group/other read
        result = verify_restrictive_permissions(f, require_owner_only=False, is_directory=False)
        assert result is True

    @unix_only
    def test_file_with_group_write(self, tmp_path):
        """Cover lines 105: file with group write fails relaxed check."""
        f = tmp_path / "file"
        f.write_text("test")
        os.chmod(str(f), 0o664)
        result = verify_restrictive_permissions(f, require_owner_only=False, is_directory=False)
        assert result is False

    @unix_only
    def test_os_error_returns_false(self, tmp_path):
        """Cover line 107: OSError during stat returns False."""
        f = tmp_path / "file"
        f.write_text("test")
        original_stat = os.stat
        call_count = 0

        def patched_stat(path, *args, **kwargs):
            nonlocal call_count
            # Let path.exists() call through (first call), fail on the explicit os.stat call
            if str(path) == str(f):
                call_count += 1
                if call_count > 1:
                    raise OSError("permission denied")
            return original_stat(path, *args, **kwargs)

        with patch("os.stat", side_effect=patched_stat):
            result = plat.verify_restrictive_permissions(f)
            assert result is False


class TestEnforceRestrictivePermissions:
    def test_nonexistent_path(self, tmp_path):
        """Cover line 128: nonexistent path does nothing."""
        p = tmp_path / "nonexistent"
        enforce_restrictive_permissions(p)  # Should not raise

    @unix_only
    def test_strict_mode_raises(self, tmp_path):
        """Cover lines 131-146: strict mode raises PermissionError."""
        f = tmp_path / "file"
        f.write_text("test")
        os.chmod(str(f), 0o644)

        with patch.dict(os.environ, {"OVERBLICK_STRICT_PERMISSIONS": "1"}):
            with pytest.raises(PermissionError, match="overly permissive"):
                enforce_restrictive_permissions(f)

    @unix_only
    def test_warning_mode_logs(self, tmp_path, caplog):
        """Cover lines 131-146: non-strict mode logs warning."""
        import logging

        f = tmp_path / "file"
        f.write_text("test")
        os.chmod(str(f), 0o644)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_STRICT_PERMISSIONS", None)
            with caplog.at_level(logging.WARNING, logger="overblick.shared.platform"):
                enforce_restrictive_permissions(f)
            assert "overly permissive" in caplog.text

    @unix_only
    def test_directory_strict_mode(self, tmp_path):
        """Cover directory permissions in strict mode."""
        d = tmp_path / "dir"
        d.mkdir()
        os.chmod(str(d), 0o755)

        with patch.dict(os.environ, {"OVERBLICK_STRICT_PERMISSIONS": "1"}):
            with pytest.raises(PermissionError):
                enforce_restrictive_permissions(d, is_directory=True)

    @unix_only
    def test_relaxed_not_owner_only_description(self, tmp_path, caplog):
        """Cover the 'no group/other write' description in warning."""
        import logging

        f = tmp_path / "file"
        f.write_text("test")
        os.chmod(str(f), 0o666)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERBLICK_STRICT_PERMISSIONS", None)
            with caplog.at_level(logging.WARNING, logger="overblick.shared.platform"):
                enforce_restrictive_permissions(f, require_owner_only=False)
            assert "no group/other write" in caplog.text


class TestRegisterShutdownSignals:
    @pytest.mark.asyncio
    async def test_windows_no_sigbreak(self):
        """Cover line 167 variant: Windows without SIGBREAK attribute."""
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        registered_signals = []

        def mock_signal_fn(signum, handler):
            registered_signals.append(signum)

        with (
            patch.object(plat, "IS_WINDOWS", True),
            patch.object(plat.signal, "signal", side_effect=mock_signal_fn),
        ):
            # Remove SIGBREAK if it exists
            had_sigbreak = hasattr(plat.signal, "SIGBREAK")
            if had_sigbreak:
                saved = getattr(plat.signal, "SIGBREAK")
                delattr(plat.signal, "SIGBREAK")
            try:
                plat.register_shutdown_signals(event, loop)
            finally:
                if had_sigbreak:
                    plat.signal.SIGBREAK = saved

        assert signal.SIGINT in registered_signals


class TestGetPythonExecutable:
    def test_windows_python_exe(self, tmp_path):
        """Cover line 189: Windows python.exe path."""
        fake_python = tmp_path / "python.exe"
        fake_python.write_text("fake")

        with (
            patch.object(plat, "IS_WINDOWS", True),
            patch("overblick.shared.platform.sysconfig.get_path", return_value=str(tmp_path)),
        ):
            result = plat.get_python_executable()
            assert result == str(fake_python)
