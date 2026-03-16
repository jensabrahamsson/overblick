"""Tests for dashboard supervisor service."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.dashboard.services.supervisor import SupervisorService


class TestSupervisorServiceInit:
    def test_should_init_with_socket_dir(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        assert svc._socket_dir == tmp_path

    def test_should_init_without_socket_dir(self):
        svc = SupervisorService()
        assert svc._socket_dir is None


class TestSupervisorServiceResolveSocketDir:
    def test_should_resolve_provided_socket_dir(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        result = svc._resolve_socket_dir()
        assert result == tmp_path

    def test_should_cache_resolved_dir(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        result1 = svc._resolve_socket_dir()
        result2 = svc._resolve_socket_dir()
        assert result1 is result2

    def test_should_resolve_default_socket_dir_when_none(self):
        svc = SupervisorService(socket_dir=None)
        result = svc._resolve_socket_dir()
        assert result.name == "ipc"
        assert result.parent.name == "data"


class TestSupervisorServiceReadAuthToken:
    def test_should_read_token_when_available(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        with patch("overblick.supervisor.ipc.read_ipc_token", return_value="secret-token"):
            token = svc._read_auth_token(tmp_path)
        assert token == "secret-token"

    def test_should_return_empty_when_token_unavailable(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        with patch("overblick.supervisor.ipc.read_ipc_token", side_effect=FileNotFoundError):
            token = svc._read_auth_token(tmp_path)
        assert token == ""


class TestSupervisorServiceGetClient:
    def test_should_create_client_unix_socket(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        mock_client = MagicMock()

        with patch("overblick.supervisor.ipc.read_ipc_token", return_value="token"), \
             patch("overblick.shared.platform.IS_WINDOWS", False), \
             patch("overblick.supervisor.ipc.IPCClient", return_value=mock_client) as mock_cls, \
             patch("overblick.supervisor.ipc._read_conn_file", return_value=None):
            client = svc._get_client()

        assert client is mock_client

    def test_should_create_client_with_tcp_port_on_windows(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        mock_client = MagicMock()
        conn_path = tmp_path / "overblick-supervisor.conn"
        conn_path.write_text("{}")  # Create the file so it exists

        with patch("overblick.supervisor.ipc.read_ipc_token", return_value=""), \
             patch("overblick.shared.platform.IS_WINDOWS", True), \
             patch("overblick.supervisor.ipc._read_conn_file", return_value={"port": 9999, "token": "conn-token"}), \
             patch("overblick.supervisor.ipc.IPCClient", return_value=mock_client) as mock_cls:
            client = svc._get_client()

        assert client is mock_client
        mock_cls.assert_called_once_with(
            target="supervisor",
            socket_dir=tmp_path,
            auth_token="conn-token",
            tcp_port=9999,
        )

    def test_should_use_conn_file_when_exists(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)
        mock_client = MagicMock()
        conn_path = tmp_path / "overblick-supervisor.conn"
        conn_path.write_text("{}")

        with patch("overblick.supervisor.ipc.read_ipc_token", return_value="existing-token"), \
             patch("overblick.shared.platform.IS_WINDOWS", False), \
             patch("overblick.supervisor.ipc._read_conn_file", return_value={"port": 8888}), \
             patch("overblick.supervisor.ipc.IPCClient", return_value=mock_client) as mock_cls:
            client = svc._get_client()

        # Should use existing auth_token, not from conn file (since auth_token is non-empty)
        mock_cls.assert_called_once_with(
            target="supervisor",
            socket_dir=tmp_path,
            auth_token="existing-token",
            tcp_port=8888,
        )

    def test_should_return_none_when_client_creation_fails(self, tmp_path):
        svc = SupervisorService(socket_dir=tmp_path)

        with patch("overblick.supervisor.ipc.read_ipc_token", return_value=""), \
             patch("overblick.shared.platform.IS_WINDOWS", False), \
             patch("overblick.supervisor.ipc._read_conn_file", return_value=None), \
             patch("overblick.supervisor.ipc.IPCClient", side_effect=RuntimeError("failed")):
            client = svc._get_client()

        assert client is None


class TestSupervisorServiceGetStatus:
    @pytest.mark.asyncio
    async def test_should_return_cached_status(self):
        svc = SupervisorService()
        svc._status_cache = {"state": "running"}
        svc._status_cache_time = time.monotonic()

        result = await svc.get_status()
        assert result == {"state": "running"}

    @pytest.mark.asyncio
    async def test_should_return_none_when_no_client(self):
        svc = SupervisorService()
        with patch.object(svc, "_get_client", return_value=None):
            result = await svc.get_status()
        assert result is None

    @pytest.mark.asyncio
    async def test_should_fetch_and_cache_status(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_client.request_status.return_value = {"state": "running", "agents": {}}

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.get_status()

        assert result == {"state": "running", "agents": {}}
        assert svc._status_cache == {"state": "running", "agents": {}}

    @pytest.mark.asyncio
    async def test_should_not_cache_none_status(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_client.request_status.return_value = None

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.get_status()

        assert result is None
        assert svc._status_cache is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_exception(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_client.request_status.side_effect = ConnectionError("refused")

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.get_status()

        assert result is None

    @pytest.mark.asyncio
    async def test_should_bypass_cache_when_expired(self):
        svc = SupervisorService()
        svc._status_cache = {"state": "old"}
        svc._status_cache_time = time.monotonic() - 10.0  # Expired

        mock_client = AsyncMock()
        mock_client.request_status.return_value = {"state": "new"}

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.get_status()

        assert result == {"state": "new"}


class TestSupervisorServiceIsRunning:
    @pytest.mark.asyncio
    async def test_should_return_true_when_running(self):
        svc = SupervisorService()
        with patch.object(svc, "get_status", return_value={"state": "running"}):
            assert await svc.is_running() is True

    @pytest.mark.asyncio
    async def test_should_return_false_when_not_running(self):
        svc = SupervisorService()
        with patch.object(svc, "get_status", return_value=None):
            assert await svc.is_running() is False


class TestSupervisorServiceGetAgents:
    @pytest.mark.asyncio
    async def test_should_return_agents_list_from_dict(self):
        svc = SupervisorService()
        status = {
            "agents": {
                "anomal": {"state": "running", "pid": 123},
                "cherry": {"state": "stopped", "pid": 0},
            }
        }
        with patch.object(svc, "get_status", return_value=status):
            agents = await svc.get_agents()

        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"anomal", "cherry"}
        anomal = next(a for a in agents if a["name"] == "anomal")
        assert anomal["state"] == "running"

    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_status(self):
        svc = SupervisorService()
        with patch.object(svc, "get_status", return_value=None):
            agents = await svc.get_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_should_return_empty_when_agents_not_dict(self):
        svc = SupervisorService()
        with patch.object(svc, "get_status", return_value={"agents": "invalid"}):
            agents = await svc.get_agents()
        assert agents == []


class TestSupervisorServiceStartAgent:
    @pytest.mark.asyncio
    async def test_should_return_error_when_no_client(self):
        svc = SupervisorService()
        with patch.object(svc, "_get_client", return_value=None):
            result = await svc.start_agent("anomal")
        assert result["success"] is False
        assert "not reachable" in result["error"]

    @pytest.mark.asyncio
    async def test_should_start_agent_successfully(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.msg_type = "agent_action_response"
        mock_response.payload = {"success": True}
        mock_client.send.return_value = mock_response

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage") as mock_msg_cls:
            result = await svc.start_agent("anomal")

        assert result == {"success": True}
        assert svc._status_cache is None  # Cache invalidated

    @pytest.mark.asyncio
    async def test_should_return_error_when_no_response(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_client.send.return_value = None

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage"):
            result = await svc.start_agent("anomal")

        assert result["success"] is False
        assert "No response" in result["error"]

    @pytest.mark.asyncio
    async def test_should_return_error_when_wrong_response_type(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.msg_type = "error"
        mock_response.payload = {}
        mock_client.send.return_value = mock_response

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage"):
            result = await svc.start_agent("anomal")

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_should_handle_exception_during_start(self):
        svc = SupervisorService()
        svc._status_cache = {"state": "running"}
        mock_client = AsyncMock()
        mock_client.send.side_effect = ConnectionError("broken")

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage"):
            result = await svc.start_agent("anomal")

        assert result["success"] is False
        assert "broken" in result["error"]
        assert svc._status_cache is None  # Cache invalidated even on error


class TestSupervisorServiceStopAgent:
    @pytest.mark.asyncio
    async def test_should_return_error_when_no_client(self):
        svc = SupervisorService()
        with patch.object(svc, "_get_client", return_value=None):
            result = await svc.stop_agent("anomal")
        assert result["success"] is False
        assert "not reachable" in result["error"]

    @pytest.mark.asyncio
    async def test_should_stop_agent_successfully(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.msg_type = "agent_action_response"
        mock_response.payload = {"success": True}
        mock_client.send.return_value = mock_response

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage") as mock_msg_cls:
            result = await svc.stop_agent("anomal")

        assert result == {"success": True}
        assert svc._status_cache is None

    @pytest.mark.asyncio
    async def test_should_return_error_when_no_response_on_stop(self):
        svc = SupervisorService()
        mock_client = AsyncMock()
        mock_client.send.return_value = None

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage"):
            result = await svc.stop_agent("anomal")

        assert result["success"] is False
        assert "No response" in result["error"]

    @pytest.mark.asyncio
    async def test_should_handle_exception_during_stop(self):
        svc = SupervisorService()
        svc._status_cache = {"state": "running"}
        mock_client = AsyncMock()
        mock_client.send.side_effect = TimeoutError("timeout")

        with patch.object(svc, "_get_client", return_value=mock_client), \
             patch("overblick.supervisor.ipc.IPCMessage"):
            result = await svc.stop_agent("anomal")

        assert result["success"] is False
        assert "timeout" in result["error"]
        assert svc._status_cache is None


class TestSupervisorServiceClose:
    @pytest.mark.asyncio
    async def test_should_close_without_error(self):
        svc = SupervisorService()
        await svc.close()  # Should not raise


class TestSupervisorServiceInvalidateCache:
    def test_should_invalidate_cache(self):
        svc = SupervisorService()
        svc._status_cache = {"state": "running"}
        svc._invalidate_cache()
        assert svc._status_cache is None
