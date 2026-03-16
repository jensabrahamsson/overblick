"""Tests for AgentProcess — subprocess lifecycle management.

Covers: start (running, exception), stop (timeout, exception, not running),
monitor (stopping, clean exit, crash), uptime, is_alive, to_dict.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.supervisor.process import AgentProcess, ProcessState


class TestStart:
    @pytest.mark.asyncio
    async def test_start_already_running(self):
        """Starting a running process returns False."""
        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc.pid = 1234
        result = await proc.start()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_exception(self):
        """Start sets state to CRASHED on exception."""
        proc = AgentProcess(identity="test")
        with patch(
            "overblick.shared.platform.get_python_executable",
            side_effect=RuntimeError("No python"),
        ):
            result = await proc.start()
        assert result is False
        assert proc.state == ProcessState.CRASHED

    @pytest.mark.asyncio
    async def test_start_success(self):
        """Successful start sets state to RUNNING."""
        mock_subprocess = AsyncMock()
        mock_subprocess.pid = 9999

        proc = AgentProcess(identity="test")
        with patch(
            "overblick.shared.platform.get_python_executable",
            return_value="/usr/bin/python3",
        ), patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_subprocess,
        ):
            result = await proc.start()

        assert result is True
        assert proc.state == ProcessState.RUNNING
        assert proc.pid == 9999
        assert proc.started_at is not None


    @pytest.mark.asyncio
    async def test_start_with_ipc_socket_dir(self):
        """Start propagates ipc_socket_dir to env."""
        mock_subprocess = AsyncMock()
        mock_subprocess.pid = 8888

        proc = AgentProcess(identity="test", ipc_socket_dir="/tmp/ipc")
        with patch(
            "overblick.shared.platform.get_python_executable",
            return_value="/usr/bin/python3",
        ), patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_subprocess,
        ) as mock_exec:
            result = await proc.start()

        assert result is True
        # Check env was passed with IPC dir
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["env"]["OVERBLICK_IPC_DIR"] == "/tmp/ipc"


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_not_running(self):
        """Stopping a non-running process returns False."""
        proc = AgentProcess(identity="test")
        result = await proc.stop()
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_graceful(self):
        """Graceful stop terminates the process."""
        mock_subprocess = AsyncMock()
        mock_subprocess.terminate = MagicMock()
        mock_subprocess.kill = MagicMock()
        mock_subprocess.wait = AsyncMock()

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc._process = mock_subprocess

        result = await proc.stop()
        assert result is True
        assert proc.state == ProcessState.STOPPED
        assert proc.stopped_at is not None
        mock_subprocess.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_timeout_force_kill(self):
        """When graceful stop times out, process is killed."""
        mock_subprocess = AsyncMock()
        mock_subprocess.terminate = MagicMock()
        mock_subprocess.kill = MagicMock()
        mock_subprocess.wait = AsyncMock(side_effect=[TimeoutError, None])

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc._process = mock_subprocess

        with patch("asyncio.wait_for", side_effect=TimeoutError):
            # After timeout, kill is called, then wait succeeds
            mock_subprocess.wait = AsyncMock()
            result = await proc.stop()

        assert result is True
        mock_subprocess.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_exception(self):
        """Stop sets state to CRASHED on exception."""
        mock_subprocess = AsyncMock()
        mock_subprocess.terminate = MagicMock(side_effect=RuntimeError("Cannot terminate"))
        mock_subprocess.wait = AsyncMock()

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc._process = mock_subprocess

        result = await proc.stop()
        assert result is False
        assert proc.state == ProcessState.CRASHED


class TestMonitor:
    @pytest.mark.asyncio
    async def test_monitor_no_process(self):
        """Monitor returns None when no process."""
        proc = AgentProcess(identity="test")
        result = await proc.monitor()
        assert result is None

    @pytest.mark.asyncio
    async def test_monitor_stopping_state(self):
        """Monitor sets STOPPED when state is STOPPING."""
        mock_subprocess = AsyncMock()
        mock_subprocess.wait = AsyncMock(return_value=0)

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.STOPPING
        proc._process = mock_subprocess

        code = await proc.monitor()
        assert code == 0
        assert proc.state == ProcessState.STOPPED

    @pytest.mark.asyncio
    async def test_monitor_clean_exit(self):
        """Monitor sets STOPPED on exit code 0."""
        mock_subprocess = AsyncMock()
        mock_subprocess.wait = AsyncMock(return_value=0)

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc._process = mock_subprocess

        code = await proc.monitor()
        assert code == 0
        assert proc.state == ProcessState.STOPPED

    @pytest.mark.asyncio
    async def test_monitor_crash(self):
        """Monitor sets CRASHED on non-zero exit code."""
        mock_subprocess = AsyncMock()
        mock_subprocess.wait = AsyncMock(return_value=1)

        proc = AgentProcess(identity="test")
        proc.state = ProcessState.RUNNING
        proc._process = mock_subprocess

        code = await proc.monitor()
        assert code == 1
        assert proc.state == ProcessState.CRASHED
        assert proc.stopped_at is not None


class TestProperties:
    def test_uptime_not_started(self):
        """Uptime is 0 when not started."""
        proc = AgentProcess(identity="test")
        assert proc.uptime_seconds == 0.0

    def test_uptime_running(self):
        """Uptime is positive when started."""
        import time

        proc = AgentProcess(identity="test")
        proc.started_at = time.time() - 10.0
        assert proc.uptime_seconds >= 9.0

    def test_uptime_stopped(self):
        """Uptime uses stopped_at when available."""
        proc = AgentProcess(identity="test")
        proc.started_at = 1000.0
        proc.stopped_at = 1010.0
        assert proc.uptime_seconds == 10.0

    def test_is_alive_no_process(self):
        """is_alive is False with no subprocess."""
        proc = AgentProcess(identity="test")
        assert proc.is_alive is False

    def test_is_alive_running(self):
        """is_alive is True when returncode is None."""
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = None

        proc = AgentProcess(identity="test")
        proc._process = mock_subprocess
        assert proc.is_alive is True

    def test_is_alive_exited(self):
        """is_alive is False when returncode is set."""
        mock_subprocess = MagicMock()
        mock_subprocess.returncode = 0

        proc = AgentProcess(identity="test")
        proc._process = mock_subprocess
        assert proc.is_alive is False


class TestToDict:
    def test_to_dict(self):
        """to_dict returns serializable dict."""
        proc = AgentProcess(identity="test", plugins=["moltbook", "github"])
        proc.state = ProcessState.RUNNING
        proc.pid = 1234
        proc.started_at = 1000.0
        proc.restart_count = 2

        d = proc.to_dict()
        assert d["identity"] == "test"
        assert d["plugins"] == ["moltbook", "github"]
        assert d["state"] == "running"
        assert d["pid"] == 1234
        assert d["restart_count"] == 2
        assert isinstance(d["uptime_seconds"], float)
