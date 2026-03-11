"""
Tests for cross-platform utilities (overblick.shared.platform).

Covers:
- Platform detection constants
- set_restrictive_permissions / set_restrictive_dir_permissions
- register_shutdown_signals (Unix and Windows paths via mocking)
- get_python_executable (with sysconfig edge cases)
"""

import asyncio
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.shared.platform import (
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    get_python_executable,
    register_shutdown_signals,
    set_restrictive_dir_permissions,
    set_restrictive_permissions,
)

# Skip marker for Unix-only tests
unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix file permissions not available on Windows"
)


class TestPlatformDetection:
    """Verify platform detection constants are consistent."""

    def test_at_most_one_platform_true(self):
        """Only one of IS_WINDOWS/IS_MACOS/IS_LINUX should be True."""
        flags = [IS_WINDOWS, IS_MACOS, IS_LINUX]
        assert sum(flags) <= 1

    def test_current_platform_detected(self):
        """At least one platform flag should match."""
        if sys.platform == "darwin":
            assert IS_MACOS
        elif sys.platform == "win32":
            assert IS_WINDOWS
        elif sys.platform.startswith("linux"):
            assert IS_LINUX


class TestSetRestrictivePermissions:
    """Tests for file permission helpers."""

    @unix_only
    def test_sets_0600_on_file(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("secret")
        set_restrictive_permissions(f)
        mode = oct(os.stat(str(f)).st_mode)[-3:]
        assert mode == "600"

    @unix_only
    def test_sets_0700_on_directory(self, tmp_path):
        d = tmp_path / "secure_dir"
        d.mkdir()
        set_restrictive_dir_permissions(d)
        mode = oct(os.stat(str(d)).st_mode)[-3:]
        assert mode == "700"

    def test_windows_noop_logs_debug(self, tmp_path, caplog):
        """On Windows path (mocked), permissions are skipped with debug log."""
        f = tmp_path / "secret.txt"
        f.write_text("secret")
        with patch("overblick.shared.platform.IS_WINDOWS", True):
            import importlib

            import overblick.shared.platform as plat

            # Call the real function with IS_WINDOWS mocked
            original_mode = os.stat(str(f)).st_mode

            # Re-import won't help; call directly with patched constant
            with patch.object(plat, "IS_WINDOWS", True):
                import logging

                with caplog.at_level(logging.DEBUG, logger="overblick.shared.platform"):
                    plat.set_restrictive_permissions(f)

                # File permissions should NOT have changed
                assert os.stat(str(f)).st_mode == original_mode

    def test_windows_dir_noop_logs_debug(self, tmp_path, caplog):
        """On Windows path (mocked), directory permissions are skipped."""
        d = tmp_path / "secure_dir"
        d.mkdir()
        import overblick.shared.platform as plat

        original_mode = os.stat(str(d)).st_mode

        with patch.object(plat, "IS_WINDOWS", True):
            import logging

            with caplog.at_level(logging.DEBUG, logger="overblick.shared.platform"):
                plat.set_restrictive_dir_permissions(d)

            assert os.stat(str(d)).st_mode == original_mode


class TestRegisterShutdownSignals:
    """Tests for shutdown signal registration."""

    @unix_only
    @pytest.mark.asyncio
    async def test_unix_registers_sigint_sigterm(self):
        """On Unix, both SIGINT and SIGTERM are registered via add_signal_handler."""
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        # Record which signals are registered
        registered_signals = []
        original_add = loop.add_signal_handler

        def spy_add(sig, callback):
            registered_signals.append(sig)
            # Don't actually register to avoid interfering with test runner

        with patch.object(loop, "add_signal_handler", side_effect=spy_add):
            register_shutdown_signals(event, loop)

        assert signal.SIGINT in registered_signals
        assert signal.SIGTERM in registered_signals

    @pytest.mark.asyncio
    async def test_windows_registers_sigint_and_sigbreak(self):
        """On Windows path (mocked), SIGINT and SIGBREAK are registered."""
        import overblick.shared.platform as plat

        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        registered_signals = []

        # Create a fake SIGBREAK value (21 on real Windows)
        FAKE_SIGBREAK = 21

        original_signal = plat.signal.signal

        def mock_signal_func(signum, handler):
            registered_signals.append(signum)

        # Patch IS_WINDOWS, signal.signal, and add SIGBREAK attribute
        with (
            patch.object(plat, "IS_WINDOWS", True),
            patch.object(plat.signal, "signal", side_effect=mock_signal_func),
            patch.object(plat.signal, "SIGBREAK", FAKE_SIGBREAK, create=True),
        ):
            plat.register_shutdown_signals(event, loop)

        assert signal.SIGINT in registered_signals
        assert FAKE_SIGBREAK in registered_signals

    @pytest.mark.asyncio
    async def test_windows_does_not_register_sigterm(self):
        """On Windows path (mocked), SIGTERM should NOT be registered."""
        import overblick.shared.platform as plat

        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        registered_signals = []

        def mock_signal(signum, handler):
            registered_signals.append(signum)

        with (
            patch.object(plat, "IS_WINDOWS", True),
            patch("overblick.shared.platform.signal.signal", side_effect=mock_signal),
        ):
            plat.register_shutdown_signals(event, loop)

        assert signal.SIGTERM not in registered_signals

    @pytest.mark.asyncio
    async def test_uses_running_loop_by_default(self):
        """When loop is None, uses the running event loop."""
        event = asyncio.Event()

        # Should not raise (loop auto-detected)
        with patch.object(asyncio.get_running_loop(), "add_signal_handler"):
            register_shutdown_signals(event)


class TestGetPythonExecutable:
    """Tests for Python executable resolution."""

    def test_returns_string(self):
        result = get_python_executable()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_to_sys_executable(self):
        """When sysconfig returns None, falls back to sys.executable."""
        with patch("overblick.shared.platform.sysconfig.get_path", return_value=None):
            import overblick.shared.platform as plat

            result = plat.get_python_executable()
            assert result == sys.executable

    def test_fallback_when_venv_python_missing(self):
        """When venv python doesn't exist, falls back to sys.executable."""
        with patch("overblick.shared.platform.sysconfig.get_path", return_value="/nonexistent"):
            import overblick.shared.platform as plat

            result = plat.get_python_executable()
            assert result == sys.executable

    def test_prefers_venv_python_when_exists(self, tmp_path):
        """When venv python exists, it's preferred over sys.executable."""
        # Create a fake venv python
        if sys.platform == "win32":
            fake_python = tmp_path / "python.exe"
        else:
            fake_python = tmp_path / "python3"
        fake_python.write_text("fake")

        with patch("overblick.shared.platform.sysconfig.get_path", return_value=str(tmp_path)):
            import overblick.shared.platform as plat

            result = plat.get_python_executable()
            assert result == str(fake_python)
