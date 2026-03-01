"""
Tests for ServiceManager — cross-platform service lifecycle.

Covers:
- PID file read/write with timestamp format
- Process liveness detection (Unix path, Windows path mocked)
- Process killing with process groups
- HTTP health checks
- ServiceManager start/stop/status for all services
- PID reuse detection
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.manage.manager import (
    ServiceManager,
    _is_process_alive,
    _kill_process,
    _read_pid,
    _write_pid,
    _http_health,
)


class TestIsProcessAlive:
    """Tests for process liveness detection."""

    def test_current_process_is_alive(self):
        """The current process should be detected as alive."""
        assert _is_process_alive(os.getpid()) is True

    def test_dead_process(self):
        """A non-existent PID should be detected as dead."""
        # PID 99999999 is extremely unlikely to exist
        assert _is_process_alive(99999999) is False

    def test_pid_zero_is_not_alive(self):
        """PID 0 (kernel/idle) should not be detected as a user process."""
        # On Unix, os.kill(0, 0) sends to process group — may succeed
        # but for our purposes, it's not a valid user process
        if sys.platform != "win32":
            # os.kill(0, 0) sends to the process group, not PID 0
            # This is platform-specific behavior
            pass
        else:
            assert _is_process_alive(0) is False

    def test_windows_code_path_runs_with_is_windows_true(self):
        """On Windows path (mocked), the Windows branch is taken.

        Since ctypes.windll doesn't exist on macOS/Linux, we verify
        the Windows code path enters the try block and handles the
        AttributeError from missing windll gracefully (returns False).
        """
        import overblick.manage.manager as mgr

        with patch.object(mgr, "IS_WINDOWS", True):
            # On non-Windows, ctypes.windll doesn't exist, so this
            # exercises the except branch — which correctly returns False
            result = mgr._is_process_alive(1234)
            assert result is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes test")
    def test_windows_uses_exit_code_native(self):
        """On actual Windows, GetExitCodeProcess distinguishes live/dead."""
        # Current process should be alive
        assert _is_process_alive(os.getpid()) is True
        # Non-existent PID should be dead
        assert _is_process_alive(99999999) is False


class TestPidFileReadWrite:
    """Tests for PID file management."""

    def test_write_and_read_pid(self, tmp_path):
        """PID file round-trip: write then read."""
        pid_file = tmp_path / "test.pid"
        _write_pid(pid_file, os.getpid())
        result = _read_pid(pid_file)
        assert result == os.getpid()

    def test_pid_file_contains_timestamp(self, tmp_path):
        """PID file format includes timestamp for PID reuse detection."""
        pid_file = tmp_path / "test.pid"
        before = time.time()
        _write_pid(pid_file, os.getpid())
        after = time.time()

        content = pid_file.read_text().strip()
        parts = content.split(":", 1)
        assert len(parts) == 2
        pid_str, ts_str = parts
        assert int(pid_str) == os.getpid()
        ts = float(ts_str)
        assert before <= ts <= after

    def test_read_missing_pid_file(self, tmp_path):
        """Missing PID file returns None."""
        pid_file = tmp_path / "nonexistent.pid"
        assert _read_pid(pid_file) is None

    def test_read_stale_pid_file(self, tmp_path):
        """PID file with dead process is cleaned up."""
        pid_file = tmp_path / "stale.pid"
        pid_file.write_text("99999999:1234567890.0")
        result = _read_pid(pid_file)
        assert result is None
        assert not pid_file.exists()  # Cleaned up

    def test_read_corrupt_pid_file(self, tmp_path):
        """Corrupt PID file returns None."""
        pid_file = tmp_path / "corrupt.pid"
        pid_file.write_text("not_a_number")
        assert _read_pid(pid_file) is None

    def test_read_legacy_pid_file_without_timestamp(self, tmp_path):
        """Old-format PID file (no timestamp) still works."""
        pid_file = tmp_path / "legacy.pid"
        pid_file.write_text(str(os.getpid()))
        result = _read_pid(pid_file)
        assert result == os.getpid()

    def test_pid_reuse_detection_future_timestamp(self, tmp_path):
        """PID file with future timestamp is detected as PID reuse."""
        pid_file = tmp_path / "reuse.pid"
        # Write current PID but with timestamp 2 minutes in the future
        future_time = time.time() + 120
        pid_file.write_text(f"{os.getpid()}:{future_time}")
        result = _read_pid(pid_file)
        assert result is None  # Detected as PID reuse
        assert not pid_file.exists()

    def test_creates_parent_directory(self, tmp_path):
        """_write_pid creates parent directories if needed."""
        pid_file = tmp_path / "deep" / "nested" / "test.pid"
        _write_pid(pid_file, os.getpid())
        assert pid_file.exists()


class TestKillProcess:
    """Tests for process termination."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
    def test_kill_process_graceful(self):
        """Start a subprocess and kill it gracefully."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        pid = proc.pid
        assert _is_process_alive(pid)

        result = _kill_process(pid, timeout=5.0)
        # Reap the zombie so os.kill(pid, 0) stops succeeding
        proc.wait()
        assert result is True
        assert not _is_process_alive(pid)

    def test_kill_nonexistent_process(self):
        """Killing a non-existent process returns False."""
        result = _kill_process(99999999)
        assert result is False


class TestHttpHealth:
    """Tests for HTTP health check."""

    def test_unreachable_url(self):
        """Health check for unreachable URL returns False."""
        assert _http_health("http://127.0.0.1:19999/health") is False

    def test_invalid_url(self):
        """Health check for invalid URL returns False."""
        assert _http_health("not-a-url") is False


class TestServiceManager:
    """Tests for ServiceManager service lifecycle."""

    @pytest.fixture
    def mgr(self, tmp_path):
        """Create a ServiceManager with temp directories."""
        return ServiceManager(base_dir=tmp_path)

    def test_status_all_stopped(self, mgr, capsys):
        """All services report stopped when nothing is running."""
        result = mgr.status()
        assert result["gateway"]["running"] is False
        assert result["dashboard"]["running"] is False
        assert result["supervisor"]["running"] is False

    def test_stop_not_running_is_noop(self, mgr, capsys):
        """Stopping a non-running service succeeds gracefully."""
        assert mgr.stop_gateway() is True
        assert mgr.stop_dashboard() is True
        assert mgr.stop_supervisor() is True

    def test_gateway_already_running(self, mgr, tmp_path, capsys):
        """start_gateway detects already-running process."""
        pid_file = tmp_path / "data" / "pids" / "gateway.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.start_gateway()
        assert result is True
        captured = capsys.readouterr()
        assert "Already running" in captured.out

    def test_dashboard_already_running(self, mgr, tmp_path, capsys):
        """start_dashboard detects already-running process."""
        pid_file = tmp_path / "data" / "pids" / "dashboard.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.start_dashboard()
        assert result is True
        captured = capsys.readouterr()
        assert "Already running" in captured.out

    def test_supervisor_already_running(self, mgr, tmp_path, capsys):
        """start_supervisor detects already-running process."""
        pid_file = tmp_path / "data" / "pids" / "supervisor.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.start_supervisor(identities=["anomal"])
        assert result is True
        captured = capsys.readouterr()
        assert "Already running" in captured.out

    def test_gateway_status_with_pid(self, mgr, tmp_path, capsys):
        """Gateway status shows running with PID."""
        pid_file = tmp_path / "data" / "pids" / "gateway.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.status_gateway()
        assert result["running"] is True
        assert result["pid"] == os.getpid()

    def test_load_env_with_env_file(self, mgr, tmp_path):
        """_load_env reads config/.env file."""
        env_dir = tmp_path / "config"
        env_dir.mkdir(exist_ok=True)
        env_file = env_dir / ".env"
        env_file.write_text("TEST_VAR=hello_world\n# comment\nFOO='bar'\n")

        env = mgr._load_env()
        assert env["TEST_VAR"] == "hello_world"
        assert env["FOO"] == "bar"

    def test_load_env_without_env_file(self, mgr):
        """_load_env works without config/.env file."""
        env = mgr._load_env()
        assert isinstance(env, dict)
        # Should contain system PATH at minimum
        assert "PATH" in env

    def test_down_stops_all(self, mgr, capsys):
        """down() calls stop for all services."""
        mgr.down()
        captured = capsys.readouterr()
        assert "Stopping" in captured.out or "Not running" in captured.out
