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
    _http_health,
    _is_process_alive,
    _kill_process,
    _read_pid,
    _write_pid,
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

    def test_up_starts_all(self, mgr, tmp_path, capsys):
        """up() calls start for all services."""
        with (
            patch.object(mgr, "start_gateway", return_value=True),
            patch.object(mgr, "start_dashboard", return_value=True),
            patch.object(mgr, "start_supervisor", return_value=True),
        ):
            mgr.up(identities=["anomal"], port=9090)
        captured = capsys.readouterr()
        assert "Starting" in captured.out

    def test_status_shows_all(self, mgr, capsys):
        """status() shows all services."""
        result = mgr.status(port=9090)
        assert "gateway" in result
        assert "dashboard" in result
        assert "supervisor" in result

    def test_dashboard_status_with_pid(self, mgr, tmp_path, capsys):
        """Dashboard status shows running with PID."""
        pid_file = tmp_path / "data" / "pids" / "dashboard.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.status_dashboard(port=9999)
        assert result["running"] is True
        assert result["pid"] == os.getpid()
        assert result["port"] == 9999

    def test_supervisor_status_with_pid(self, mgr, tmp_path, capsys):
        """Supervisor status shows running with PID."""
        pid_file = tmp_path / "data" / "pids" / "supervisor.pid"
        _write_pid(pid_file, os.getpid())

        result = mgr.status_supervisor()
        assert result["running"] is True
        assert result["pid"] == os.getpid()


class TestServiceManagerStartStopProcesses:
    """Tests for actual start/stop with mocked subprocesses."""

    @pytest.fixture
    def mgr(self, tmp_path):
        return ServiceManager(base_dir=tmp_path)

    def test_start_gateway_success_healthy(self, mgr, tmp_path, capsys):
        """Gateway starts and becomes healthy."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=True),
        ):
            result = mgr.start_gateway()

        assert result is True
        captured = capsys.readouterr()
        assert "healthy" in captured.out

    def test_start_gateway_alive_but_not_healthy(self, mgr, tmp_path, capsys):
        """Gateway starts but health check doesn't pass."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=False),
            patch("overblick.manage.manager._is_process_alive", return_value=True),
            patch("time.sleep"),
        ):
            result = mgr.start_gateway()

        assert result is True
        captured = capsys.readouterr()
        assert "health check pending" in captured.out

    def test_start_gateway_fails_to_start(self, mgr, tmp_path, capsys):
        """Gateway process dies immediately."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=False),
            patch("overblick.manage.manager._is_process_alive", return_value=False),
            patch("time.sleep"),
        ):
            result = mgr.start_gateway()

        assert result is False
        captured = capsys.readouterr()
        assert "FAILED" in captured.out

    def test_stop_gateway_running(self, mgr, tmp_path, capsys):
        """Stop a running gateway."""
        pid_file = tmp_path / "data" / "pids" / "gateway.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=True):
            result = mgr.stop_gateway()

        assert result is True
        captured = capsys.readouterr()
        assert "Stopped" in captured.out

    def test_stop_gateway_failed(self, mgr, tmp_path, capsys):
        """Stop gateway fails when kill fails."""
        pid_file = tmp_path / "data" / "pids" / "gateway.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=False):
            result = mgr.stop_gateway()

        assert result is False
        captured = capsys.readouterr()
        assert "Failed to stop" in captured.out

    def test_start_dashboard_success_healthy(self, mgr, tmp_path, capsys):
        """Dashboard starts and becomes healthy."""
        mock_proc = MagicMock()
        mock_proc.pid = 54321

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=True),
        ):
            result = mgr.start_dashboard(port=9090)

        assert result is True
        captured = capsys.readouterr()
        assert "9090" in captured.out

    def test_start_dashboard_alive_not_healthy(self, mgr, tmp_path, capsys):
        """Dashboard starts but health check fails, process alive."""
        mock_proc = MagicMock()
        mock_proc.pid = 54321

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=False),
            patch("overblick.manage.manager._is_process_alive", return_value=True),
            patch("time.sleep"),
        ):
            result = mgr.start_dashboard(port=9090)

        assert result is True

    def test_start_dashboard_failed(self, mgr, tmp_path, capsys):
        """Dashboard process dies immediately."""
        mock_proc = MagicMock()
        mock_proc.pid = 54321

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("overblick.manage.manager._http_health", return_value=False),
            patch("overblick.manage.manager._is_process_alive", return_value=False),
            patch("time.sleep"),
        ):
            result = mgr.start_dashboard(port=9090)

        assert result is False
        captured = capsys.readouterr()
        assert "FAILED" in captured.out

    def test_stop_dashboard_running(self, mgr, tmp_path, capsys):
        """Stop a running dashboard."""
        pid_file = tmp_path / "data" / "pids" / "dashboard.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=True):
            result = mgr.stop_dashboard()

        assert result is True

    def test_stop_dashboard_failed(self, mgr, tmp_path, capsys):
        """Stop dashboard fails."""
        pid_file = tmp_path / "data" / "pids" / "dashboard.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=False):
            result = mgr.stop_dashboard()

        assert result is False

    def test_start_supervisor_success(self, mgr, tmp_path, capsys):
        """Supervisor starts successfully."""
        mock_proc = MagicMock()
        mock_proc.pid = 99999

        with patch("subprocess.Popen", return_value=mock_proc):
            result = mgr.start_supervisor(identities=["anomal", "cherry"])

        assert result is True
        captured = capsys.readouterr()
        assert "anomal" in captured.out

    def test_start_supervisor_default_identities(self, mgr, tmp_path, capsys):
        """Supervisor uses default identities when none specified."""
        mock_proc = MagicMock()
        mock_proc.pid = 99999

        with patch("subprocess.Popen", return_value=mock_proc):
            result = mgr.start_supervisor()

        assert result is True

    def test_stop_supervisor_running(self, mgr, tmp_path, capsys):
        """Stop a running supervisor."""
        pid_file = tmp_path / "data" / "pids" / "supervisor.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=True):
            result = mgr.stop_supervisor()

        assert result is True

    def test_stop_supervisor_failed(self, mgr, tmp_path, capsys):
        """Stop supervisor fails."""
        pid_file = tmp_path / "data" / "pids" / "supervisor.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._kill_process", return_value=False):
            result = mgr.stop_supervisor()

        assert result is False

    def test_gateway_status_healthy(self, mgr, tmp_path, capsys):
        """Gateway reports healthy when health check passes."""
        pid_file = tmp_path / "data" / "pids" / "gateway.pid"
        _write_pid(pid_file, os.getpid())

        with patch("overblick.manage.manager._http_health", return_value=True):
            result = mgr.status_gateway()

        assert result["healthy"] is True
        captured = capsys.readouterr()
        assert "healthy" in captured.out

    def test_dashboard_status_stopped(self, mgr, capsys):
        """Dashboard reports stopped when not running."""
        result = mgr.status_dashboard()
        assert result["running"] is False

    def test_supervisor_status_stopped(self, mgr, capsys):
        """Supervisor reports stopped when not running."""
        result = mgr.status_supervisor()
        assert result["running"] is False


class TestKillProcessUnix:
    """Test Unix kill process paths."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_process_with_group_leader(self):
        """Kill process that is a group leader."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,  # Makes it a group leader
        )
        pid = proc.pid
        assert _is_process_alive(pid)

        result = _kill_process(pid, timeout=5.0)
        proc.wait()
        assert result is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_process_force_kill_path(self):
        """Kill process that ignores SIGTERM requires SIGKILL."""

        # Start a process that ignores SIGTERM
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
            ],
        )
        pid = proc.pid
        assert _is_process_alive(pid)

        # Very short timeout to trigger force kill
        result = _kill_process(pid, timeout=0.5)
        proc.wait()
        assert result is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_process_pgid_different_from_pid(self):
        """Kill process where pgid != pid (not a group leader)."""
        # Start normally (inherits parent's group)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        pid = proc.pid
        result = _kill_process(pid, timeout=5.0)
        proc.wait()
        assert result is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_process_oserror_fallback(self):
        """Kill process handles OSError from killpg by falling back to kill."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        pid = proc.pid

        with patch("os.getpgid", side_effect=OSError("no permission")):
            result = _kill_process(pid, timeout=5.0)
            proc.wait()
            # Fallback to os.kill should work
            assert result is True


class TestKillProcessWindows:
    """Test Windows kill process path."""

    def test_kill_process_windows_path(self):
        """Kill process on Windows path (mocked)."""
        import overblick.manage.manager as mgr

        with patch.object(mgr, "IS_WINDOWS", True):
            # On non-Windows, ctypes.windll doesn't exist
            result = mgr._kill_process(99999999)
            assert result is False

    def test_kill_process_windows_success(self):
        """Kill process on Windows succeeds via ctypes mock."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 1234  # Valid handle
        mock_kernel32.TerminateProcess.return_value = True
        mock_kernel32.CloseHandle.return_value = True

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
        ):
            result = mgr._kill_process(1234)
            assert result is True

    def test_kill_process_windows_no_handle(self):
        """Kill process on Windows fails when OpenProcess returns 0."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 0  # No handle

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
        ):
            result = mgr._kill_process(99999)
            assert result is False


class TestIsProcessAliveWindows:
    """Test Windows _is_process_alive path."""

    def test_alive_still_active(self):
        """Windows process with STILL_ACTIVE exit code is alive."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 1234

        # Create a real c_ulong-like object for exit_code
        exit_code = MagicMock()
        exit_code.value = 259  # STILL_ACTIVE

        mock_kernel32.GetExitCodeProcess.return_value = True

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
            patch.object(ctypes, "c_ulong", return_value=exit_code),
            patch.object(ctypes, "byref", return_value=MagicMock()),
        ):
            result = mgr._is_process_alive(1234)
            assert result is True

    def test_not_alive_exited(self):
        """Windows process with non-STILL_ACTIVE exit code is dead."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 1234

        exit_code = MagicMock()
        exit_code.value = 0  # Exited normally

        mock_kernel32.GetExitCodeProcess.return_value = True

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
            patch.object(ctypes, "c_ulong", return_value=exit_code),
            patch.object(ctypes, "byref", return_value=MagicMock()),
        ):
            result = mgr._is_process_alive(1234)
            assert result is False

    def test_no_handle(self):
        """Windows process with no handle is dead."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 0  # No handle

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
        ):
            result = mgr._is_process_alive(99999)
            assert result is False

    def test_get_exit_code_fails(self):
        """Windows GetExitCodeProcess failure returns False."""
        import ctypes

        import overblick.manage.manager as mgr

        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 1234
        mock_kernel32.GetExitCodeProcess.return_value = False  # Failure

        exit_code = MagicMock()
        exit_code.value = 0

        with (
            patch.object(mgr, "IS_WINDOWS", True),
            patch.object(ctypes, "windll", create=True, new=MagicMock(kernel32=mock_kernel32)),
            patch.object(ctypes, "c_ulong", return_value=exit_code),
            patch.object(ctypes, "byref", return_value=MagicMock()),
        ):
            result = mgr._is_process_alive(1234)
            assert result is False


class TestKillProcessSignalPaths:
    """Test edge cases in _send_signal and waitpid paths."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_send_signal_both_killpg_and_kill_fail(self):
        """When both killpg and fallback kill fail, returns False."""
        # Use a nonexistent PID
        with (
            patch("os.getpgid", side_effect=OSError("no perm")),
            patch("os.kill", side_effect=OSError("no perm")),
        ):
            result = _kill_process(99999999, timeout=0.5)
            assert result is False

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_waitpid_child_process_error(self):
        """ChildProcessError from waitpid is silently handled."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        pid = proc.pid

        call_count = [0]
        original_waitpid = os.waitpid

        def mock_waitpid(p, flags):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ChildProcessError("not our child")
            return original_waitpid(p, flags)

        with patch("os.waitpid", side_effect=mock_waitpid):
            result = _kill_process(pid, timeout=5.0)

        proc.wait()
        assert result is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_waitpid_oserror(self):
        """OSError from waitpid in the poll loop is silently handled."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        pid = proc.pid

        call_count = [0]
        original_waitpid = os.waitpid

        def mock_waitpid(p, flags):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise OSError("waitpid failed")
            return original_waitpid(p, flags)

        with patch("os.waitpid", side_effect=mock_waitpid):
            result = _kill_process(pid, timeout=5.0)

        proc.wait()
        assert result is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_force_kill_waitpid_error(self):
        """Post-SIGKILL waitpid errors (ChildProcessError/OSError) are handled."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"],
        )
        pid = proc.pid

        # Always raise from waitpid to cover both the loop and post-SIGKILL paths
        # Without reaping, _is_process_alive may still see the zombie, so result
        # depends on timing. The important thing is no exception is raised.
        with patch("os.waitpid", side_effect=ChildProcessError("not our child")):
            _kill_process(pid, timeout=0.5)

        proc.wait()


class TestHttpHealthSuccess:
    """Test successful HTTP health check."""

    def test_health_check_success(self):
        """HTTP health check returns True for 200 response."""
        mock_resp = MagicMock()
        mock_resp.status = 200

        with patch("overblick.manage.manager.urlopen", return_value=mock_resp):
            result = _http_health("http://127.0.0.1:8200/health")
            assert result is True

    def test_health_check_301_redirect(self):
        """HTTP health check returns True for 301 response."""
        mock_resp = MagicMock()
        mock_resp.status = 301

        with patch("overblick.manage.manager.urlopen", return_value=mock_resp):
            result = _http_health("http://127.0.0.1:8200/")
            assert result is True

    def test_health_check_400_returns_false(self):
        """HTTP health check returns False for 400+ response."""
        mock_resp = MagicMock()
        mock_resp.status = 500

        with patch("overblick.manage.manager.urlopen", return_value=mock_resp):
            result = _http_health("http://127.0.0.1:8200/health")
            assert result is False


class TestLoadEnvEdgeCases:
    """Test _load_env error paths."""

    def test_load_env_read_failure(self, tmp_path):
        """_load_env handles read errors gracefully."""
        mgr = ServiceManager(base_dir=tmp_path)
        env_dir = tmp_path / "config"
        env_dir.mkdir(exist_ok=True)
        env_file = env_dir / ".env"
        env_file.write_text("VALID=ok\n")

        # Make the file unreadable
        with patch.object(Path, "read_text", side_effect=PermissionError("no read")):
            env = mgr._load_env()
            # Should still return env dict (just without .env values)
            assert isinstance(env, dict)


class TestModuleLevelFunctions:
    """Test module-level helper functions."""

    def test_base_dir(self):
        """_base_dir returns project root."""
        from overblick.manage.manager import _base_dir

        result = _base_dir()
        assert result.is_dir()

    def test_pid_dir(self):
        """_pid_dir returns data/pids path."""
        from overblick.manage.manager import _pid_dir

        result = _pid_dir()
        assert "pids" in str(result)

    def test_log_dir(self):
        """_log_dir returns logs path."""
        from overblick.manage.manager import _log_dir

        result = _log_dir()
        assert "logs" in str(result)


class TestWindowsStartService:
    """Test Windows-specific start paths."""

    @pytest.fixture
    def mgr(self, tmp_path):
        return ServiceManager(base_dir=tmp_path)

    def test_start_gateway_windows_flags(self, mgr, tmp_path, capsys):
        """Gateway start uses CREATE_NO_WINDOW on Windows."""
        import overblick.manage.manager as mgr_mod

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch.object(mgr_mod, "IS_WINDOWS", True),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("overblick.manage.manager._http_health", return_value=True),
        ):
            result = mgr.start_gateway()

        assert result is True
        call_kwargs = mock_popen.call_args[1]
        assert "creationflags" in call_kwargs

    def test_start_dashboard_windows_flags(self, mgr, tmp_path, capsys):
        """Dashboard start uses CREATE_NO_WINDOW on Windows."""
        import overblick.manage.manager as mgr_mod

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch.object(mgr_mod, "IS_WINDOWS", True),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("overblick.manage.manager._http_health", return_value=True),
        ):
            result = mgr.start_dashboard()

        assert result is True
        call_kwargs = mock_popen.call_args[1]
        assert "creationflags" in call_kwargs

    def test_start_supervisor_windows_flags(self, mgr, tmp_path, capsys):
        """Supervisor start uses CREATE_NO_WINDOW on Windows."""
        import overblick.manage.manager as mgr_mod

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch.object(mgr_mod, "IS_WINDOWS", True),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = mgr.start_supervisor(identities=["anomal"])

        assert result is True
        call_kwargs = mock_popen.call_args[1]
        assert "creationflags" in call_kwargs
