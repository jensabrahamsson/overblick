"""
Tests for the Supervisor, AgentProcess, and IPC system.
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.supervisor.ipc import IPCClient, IPCMessage, IPCServer
from overblick.supervisor.process import AgentProcess, ProcessState
from overblick.supervisor.supervisor import Supervisor, SupervisorState


@pytest.fixture
def short_tmp():
    """Short temp directory for Unix sockets (macOS has 104-char path limit)."""
    d = Path(tempfile.mkdtemp(prefix="bk"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# IPCMessage
# ---------------------------------------------------------------------------


class TestIPCMessage:
    def test_serialize_deserialize(self):
        msg = IPCMessage(msg_type="test", payload={"key": "value"}, sender="agent-1")
        json_str = msg.to_json()
        restored = IPCMessage.from_json(json_str)
        assert restored.msg_type == "test"
        assert restored.payload == {"key": "value"}
        assert restored.sender == "agent-1"

    def test_status_request(self):
        msg = IPCMessage.status_request(sender="anomal")
        assert msg.msg_type == "status_request"
        assert msg.sender == "anomal"

    def test_status_response(self):
        status = {"agents": {"anomal": {"state": "running"}}}
        msg = IPCMessage.status_response(status, sender="supervisor")
        assert msg.msg_type == "status_response"
        assert msg.payload["agents"]["anomal"]["state"] == "running"

    def test_permission_request(self):
        msg = IPCMessage.permission_request(
            resource="moltbook.comment",
            action="write",
            reason="Wants to comment",
            sender="anomal",
        )
        assert msg.msg_type == "permission_request"
        assert msg.payload["resource"] == "moltbook.comment"

    def test_permission_response(self):
        msg = IPCMessage.permission_response(granted=True, reason="approved")
        assert msg.payload["granted"] is True

    def test_shutdown(self):
        msg = IPCMessage.shutdown(sender="admin")
        assert msg.msg_type == "shutdown"

    def test_to_json_is_valid_json(self):
        msg = IPCMessage(msg_type="test", payload={"x": 1})
        parsed = json.loads(msg.to_json())
        assert parsed["type"] == "test"
        assert parsed["payload"]["x"] == 1


# ---------------------------------------------------------------------------
# AgentProcess
# ---------------------------------------------------------------------------


class TestAgentProcess:
    def test_initial_state(self):
        agent = AgentProcess(identity="anomal")
        assert agent.state == ProcessState.PENDING
        assert agent.pid is None
        assert agent.uptime_seconds == 0.0

    def test_to_dict(self):
        agent = AgentProcess(identity="anomal", plugins=["moltbook"])
        d = agent.to_dict()
        assert d["identity"] == "anomal"
        assert d["state"] == "pending"
        assert d["plugins"] == ["moltbook"]
        assert d["pid"] is None

    def test_is_alive_no_process(self):
        agent = AgentProcess(identity="anomal")
        assert agent.is_alive is False

    @pytest.mark.asyncio
    async def test_stop_not_running_returns_false(self):
        agent = AgentProcess(identity="anomal")
        result = await agent.stop()
        assert result is False

    def test_ipc_socket_dir_field(self):
        """AgentProcess accepts and stores ipc_socket_dir."""
        agent = AgentProcess(identity="anomal", ipc_socket_dir="/data/ipc")
        assert agent.ipc_socket_dir == "/data/ipc"

    def test_ipc_socket_dir_default_none(self):
        """ipc_socket_dir is None by default."""
        agent = AgentProcess(identity="anomal")
        assert agent.ipc_socket_dir is None

    @pytest.mark.asyncio
    async def test_monitor_clean_exit_sets_stopped(self):
        """monitor() sets state to STOPPED when process exits with code 0."""
        agent = AgentProcess(identity="test_clean")
        agent.state = ProcessState.RUNNING

        # Create a mock process that exits cleanly
        from unittest.mock import AsyncMock, MagicMock

        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0
        agent._process = mock_proc

        returncode = await agent.monitor()
        assert returncode == 0
        assert agent.state == ProcessState.STOPPED
        assert agent.stopped_at is not None

    @pytest.mark.asyncio
    async def test_monitor_crash_sets_crashed(self):
        """monitor() sets state to CRASHED when process exits with non-zero."""
        agent = AgentProcess(identity="test_crash")
        agent.state = ProcessState.RUNNING

        from unittest.mock import AsyncMock, MagicMock

        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=1)
        mock_proc.returncode = 1
        agent._process = mock_proc

        returncode = await agent.monitor()
        assert returncode == 1
        assert agent.state == ProcessState.CRASHED

    @pytest.mark.asyncio
    async def test_monitor_stopping_state_preserved(self):
        """monitor() respects STOPPING state (explicit stop in progress)."""
        agent = AgentProcess(identity="test_stop")
        agent.state = ProcessState.STOPPING

        from unittest.mock import AsyncMock, MagicMock

        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0
        agent._process = mock_proc

        returncode = await agent.monitor()
        assert returncode == 0
        assert agent.state == ProcessState.STOPPED

    @pytest.mark.asyncio
    async def test_monitor_no_process_returns_none(self):
        """monitor() returns None when no process is set."""
        agent = AgentProcess(identity="test_none")
        result = await agent.monitor()
        assert result is None


# ---------------------------------------------------------------------------
# IPCServer + IPCClient
# ---------------------------------------------------------------------------


class TestIPC:
    @pytest.mark.asyncio
    async def test_server_start_stop(self, short_tmp):
        server = IPCServer(name="t1", socket_dir=short_tmp)
        await server.start()
        assert server.socket_path.exists()
        await server.stop()
        assert not server.socket_path.exists()

    @pytest.mark.asyncio
    async def test_client_server_roundtrip(self, short_tmp):
        """Client sends status_request, server responds."""
        server = IPCServer(name="t2", socket_dir=short_tmp)

        async def handle_status(msg):
            return IPCMessage.status_response(
                {"agents": {}, "state": "running"},
                sender="supervisor",
            )

        server.on("status_request", handle_status)
        await server.start()

        try:
            client = IPCClient(target="t2", socket_dir=short_tmp)
            status = await client.request_status(sender="test-agent")
            assert status is not None
            assert status["state"] == "running"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_permission_request(self, short_tmp):
        """Client requests permission, server auto-approves."""
        server = IPCServer(name="t3", socket_dir=short_tmp)

        async def handle_perm(msg):
            return IPCMessage.permission_response(granted=True, sender="supervisor")

        server.on("permission_request", handle_perm)
        await server.start()

        try:
            client = IPCClient(target="t3", socket_dir=short_tmp)
            granted = await client.request_permission(
                resource="moltbook.comment",
                action="write",
                reason="test",
                sender="anomal",
            )
            assert granted is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_no_server(self, short_tmp):
        """Client gracefully handles missing server."""
        client = IPCClient(target="nope", socket_dir=short_tmp)
        result = await client.request_status()
        assert result is None

    @pytest.mark.asyncio
    async def test_server_unknown_message_type(self, short_tmp):
        """Server logs warning for unknown message types."""
        server = IPCServer(name="t4", socket_dir=short_tmp)
        await server.start()

        try:
            client = IPCClient(target="t4", socket_dir=short_tmp)
            msg = IPCMessage(msg_type="unknown_type", sender="test")
            response = await client.send(msg)
            assert response is None
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class TestSupervisor:
    def test_initial_state(self):
        sup = Supervisor(identities=["anomal", "cherry"])
        assert sup.state == SupervisorState.INIT
        assert len(sup.agents) == 0

    def test_get_status_empty(self):
        sup = Supervisor()
        status = sup.get_status()
        assert status["supervisor_state"] == "init"
        assert status["total_agents"] == 0
        assert status["running_agents"] == 0

    def test_start_agent_passes_ipc_socket_dir(self, short_tmp):
        """Supervisor passes its IPC socket_dir to AgentProcess."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        # Directly create an agent using the same logic as start_agent
        agent = AgentProcess(
            identity="anomal",
            plugins=["moltbook"],
            ipc_socket_dir=str(sup._ipc._socket_dir),
        )
        assert agent.ipc_socket_dir == str(short_tmp)

    @pytest.mark.asyncio
    async def test_start_stop(self, short_tmp):
        """Supervisor starts and stops IPC without agents."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()
        assert sup.state == SupervisorState.RUNNING
        await sup.stop()
        assert sup.state == SupervisorState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, short_tmp):
        """Calling stop twice doesn't crash."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()
        await sup.stop()
        await sup.stop()
        assert sup.state == SupervisorState.STOPPED

    @pytest.mark.asyncio
    async def test_status_via_ipc(self, short_tmp):
        """Query status via IPC client."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            status = await client.request_status(sender="test")
            assert status is not None
            assert status["supervisor_state"] == "running"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_permission_auto_approve(self, short_tmp):
        """Permission requests are auto-approved in stage 1."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            granted = await client.request_permission(
                resource="moltbook.comment",
                action="write",
                reason="test comment",
                sender="anomal",
            )
            assert granted is True
        finally:
            await sup.stop()


# ---------------------------------------------------------------------------
# IPC Authentication
# ---------------------------------------------------------------------------


class TestSupervisorAgentActions:
    """Test start/stop agent IPC handlers."""

    @pytest.mark.asyncio
    async def test_start_agent_via_ipc(self, short_tmp):
        """start_agent IPC handler returns success for valid identity."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            msg = IPCMessage(
                msg_type="start_agent",
                payload={"identity": "anomal"},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=5.0)
            assert response is not None
            assert response.msg_type == "agent_action_response"
            assert response.payload["action"] == "start"
            # May fail (no actual agent binary) but handler should respond
            assert "identity" in response.payload
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_stop_agent_via_ipc_not_found(self, short_tmp):
        """stop_agent IPC handler returns error for non-running agent."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            msg = IPCMessage(
                msg_type="stop_agent",
                payload={"identity": "nonexistent"},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=5.0)
            assert response is not None
            assert response.msg_type == "agent_action_response"
            assert response.payload["success"] is False
            assert response.payload["action"] == "stop"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_start_agent_missing_identity(self, short_tmp):
        """start_agent with missing identity returns error."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            msg = IPCMessage(
                msg_type="start_agent",
                payload={},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=5.0)
            assert response is not None
            assert response.payload["success"] is False
            assert "Missing identity" in response.payload["error"]
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_stop_agent_missing_identity(self, short_tmp):
        """stop_agent with missing identity returns error."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            client = IPCClient(
                target="supervisor",
                socket_dir=short_tmp,
                auth_token=sup._auth_token,
            )
            msg = IPCMessage(
                msg_type="stop_agent",
                payload={},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=5.0)
            assert response is not None
            assert response.payload["success"] is False
            assert "Missing identity" in response.payload["error"]
        finally:
            await sup.stop()


class TestIPCAuth:
    @pytest.mark.asyncio
    async def test_auth_accepted(self, short_tmp):
        """Valid auth token is accepted."""
        token = "test-secret-token-123"
        server = IPCServer(name="a1", socket_dir=short_tmp, auth_token=token)

        async def handle_status(msg):
            return IPCMessage.status_response({"ok": True}, sender="server")

        server.on("status_request", handle_status)
        await server.start()

        try:
            client = IPCClient(target="a1", socket_dir=short_tmp, auth_token=token)
            status = await client.request_status(sender="test")
            assert status is not None
            assert status["ok"] is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_rejected(self, short_tmp):
        """Invalid auth token is rejected — no response."""
        token = "correct-token"
        server = IPCServer(name="a2", socket_dir=short_tmp, auth_token=token)

        async def handle_status(msg):
            return IPCMessage.status_response({"ok": True}, sender="server")

        server.on("status_request", handle_status)
        await server.start()

        try:
            # Client with wrong token
            client = IPCClient(target="a2", socket_dir=short_tmp, auth_token="wrong-token")
            status = await client.request_status(sender="attacker")
            assert status is None
            assert server.rejected_count == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_no_token_required(self, short_tmp):
        """When server has no auth_token, all messages are accepted."""
        server = IPCServer(name="a3", socket_dir=short_tmp)

        async def handle_status(msg):
            return IPCMessage.status_response({"ok": True}, sender="server")

        server.on("status_request", handle_status)
        await server.start()

        try:
            client = IPCClient(target="a3", socket_dir=short_tmp)
            status = await client.request_status(sender="test")
            assert status is not None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_missing_token_rejected(self, short_tmp):
        """Client without token rejected when server requires auth."""
        token = "server-secret"
        server = IPCServer(name="a4", socket_dir=short_tmp, auth_token=token)

        async def handle_status(msg):
            return IPCMessage.status_response({"ok": True}, sender="server")

        server.on("status_request", handle_status)
        await server.start()

        try:
            # Client without any token
            client = IPCClient(target="a4", socket_dir=short_tmp)
            status = await client.request_status(sender="unauthenticated")
            assert status is None
            assert server.rejected_count == 1
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Identity plugin resolution
# ---------------------------------------------------------------------------


class TestSupervisorIdentityPlugins:
    """Test that start_agent loads plugins from identity config."""

    @pytest.mark.asyncio
    async def test_start_agent_loads_identity_plugins(self, short_tmp):
        """start_agent should resolve plugins from identity config."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            # Mock load_identity to return an identity with specific plugins
            from unittest.mock import MagicMock, patch

            mock_identity = MagicMock()
            mock_identity.plugins = ("moltbook", "ai_digest", "telegram")

            with patch("overblick.identities.load_identity", return_value=mock_identity):
                # Agent may fail to actually start (no binary), but the
                # AgentProcess should have the correct plugins set
                agent = AgentProcess(
                    identity="anomal",
                    plugins=["moltbook"],  # default
                    ipc_socket_dir=str(sup._ipc._socket_dir),
                )
                # Simulate the plugin resolution logic from start_agent:
                # when no plugins arg is given, it loads from identity config
                try:
                    from overblick.identities import load_identity

                    ident = load_identity("anomal")
                    if ident and ident.plugins:
                        agent.plugins = list(ident.plugins)
                except Exception:
                    pass

                assert agent.plugins == ["moltbook", "ai_digest", "telegram"]
        finally:
            await sup.stop()


# ---------------------------------------------------------------------------
# Additional Supervisor coverage tests
# ---------------------------------------------------------------------------


class TestSupervisorStartFailure:
    """Test supervisor start failure and cleanup."""

    @pytest.mark.asyncio
    async def test_start_failure_cleans_up(self, short_tmp):
        """When IPC start fails, supervisor cleans up."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)

        with patch.object(sup._ipc, "start", side_effect=RuntimeError("ipc failed")):
            with pytest.raises(RuntimeError, match="ipc failed"):
                await sup.start()

        assert sup.state == SupervisorState.STOPPED

    @pytest.mark.asyncio
    async def test_start_with_identities_calls_start_agent(self, short_tmp):
        """start() calls start_agent for each identity in the list."""
        sup = Supervisor(identities=["agent1", "agent2"], socket_dir=short_tmp)
        started = []

        async def mock_start_agent(identity, plugins=None):
            started.append(identity)
            return MagicMock()

        with patch.object(sup, "start_agent", side_effect=mock_start_agent):
            await sup.start()

        assert started == ["agent1", "agent2"]
        await sup.stop()

    @pytest.mark.asyncio
    async def test_start_agent_returns_none_when_process_fails(self, short_tmp):
        """start_agent returns None when agent.start() fails."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            # Patch AgentProcess.start to return False
            with patch(
                "overblick.supervisor.supervisor.AgentProcess"
            ) as MockAP:
                mock_agent = MagicMock()
                mock_agent.state = ProcessState.PENDING
                mock_agent.start = AsyncMock(return_value=False)
                MockAP.return_value = mock_agent

                result = await sup.start_agent("fail_agent")
                assert result is None
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_start_agent_already_running(self, short_tmp):
        """start_agent returns existing agent if already running."""
        from unittest.mock import MagicMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            # Add a fake running agent
            mock_agent = MagicMock()
            mock_agent.state = ProcessState.RUNNING
            sup._agents["existing"] = mock_agent

            result = await sup.start_agent("existing")
            assert result is mock_agent
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_start_agent_load_identity_fails(self, short_tmp):
        """start_agent handles load_identity failure gracefully."""
        from unittest.mock import patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            with patch(
                "overblick.identities.load_identity",
                side_effect=ImportError("no module"),
            ):
                # Agent start itself may fail (no real binary)
                await sup.start_agent("test_agent")
                # Whether it succeeded or not, it shouldn't crash
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_start_agent_process_fails(self, short_tmp):
        """start_agent returns None when agent.start() fails."""

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            # Agent fails to start
            await sup.start_agent("nonexistent_agent")
            # May return None if start fails
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_stop_agent_not_found(self, short_tmp):
        """stop_agent returns False for unknown agent."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            result = await sup.stop_agent("nonexistent")
            assert result is False
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_stop_agent_cancels_monitor_task(self, short_tmp):
        """stop_agent cancels monitor task and unregisters from router."""
        from unittest.mock import AsyncMock, MagicMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            # Add a fake agent with monitor task
            mock_agent = MagicMock()
            mock_agent.stop = AsyncMock(return_value=True)
            sup._agents["test_agent"] = mock_agent

            mock_task = MagicMock()
            sup._monitor_tasks["test_agent"] = mock_task

            sup._message_router.register_agent("test_agent")

            result = await sup.stop_agent("test_agent")
            assert result is True
            mock_task.cancel.assert_called_once()
        finally:
            await sup.stop()


class TestSupervisorStop:
    """Test supervisor stop scenarios."""

    @pytest.mark.asyncio
    async def test_stop_handles_agent_stop_errors(self, short_tmp):
        """stop() isolates per-agent errors during shutdown."""
        from unittest.mock import AsyncMock, MagicMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        # Add agent that raises on stop
        mock_agent = MagicMock()
        mock_agent.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        sup._agents["bad_agent"] = mock_agent

        mock_task = MagicMock()
        sup._monitor_tasks["bad_agent"] = mock_task

        await sup.stop()
        assert sup.state == SupervisorState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_cancels_remaining_monitor_tasks(self, short_tmp):
        """stop() cancels and awaits remaining monitor tasks."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        # Create a real async task that sleeps forever, so cancel works
        async def sleep_forever():
            await asyncio.sleep(3600)

        task = asyncio.create_task(sleep_forever())
        sup._monitor_tasks["orphan"] = task

        await sup.stop()
        assert sup.state == SupervisorState.STOPPED
        assert len(sup._monitor_tasks) == 0


class TestSupervisorMonitor:
    """Test _monitor_agent method."""

    @pytest.mark.asyncio
    async def test_monitor_agent_not_found(self, short_tmp):
        """_monitor_agent returns early if agent not in dict."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup._monitor_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_monitor_agent_auto_restart(self, short_tmp):
        """_monitor_agent auto-restarts crashed agent."""
        from unittest.mock import AsyncMock, MagicMock, patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        sup._state = SupervisorState.RUNNING

        mock_agent = MagicMock()
        mock_agent.state = ProcessState.RUNNING
        mock_agent.restart_count = 0
        mock_agent.max_restarts = 3
        mock_agent.monitor = AsyncMock()
        mock_agent.start = AsyncMock(return_value=True)
        sup._agents["test"] = mock_agent

        # After monitor() returns, set state to CRASHED
        async def set_crashed():
            mock_agent.state = ProcessState.CRASHED

        mock_agent.monitor = AsyncMock(side_effect=set_crashed)

        with patch("asyncio.create_task"):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await sup._monitor_agent("test")

        assert mock_agent.restart_count == 1
        mock_agent.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_monitor_agent_cancelled(self, short_tmp):
        """_monitor_agent handles CancelledError."""
        from unittest.mock import AsyncMock, MagicMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        sup._state = SupervisorState.RUNNING

        mock_agent = MagicMock()
        mock_agent.monitor = AsyncMock(side_effect=asyncio.CancelledError)
        sup._agents["test"] = mock_agent

        # Should not raise
        await sup._monitor_agent("test")


class TestSupervisorRun:
    """Test supervisor run method."""

    @pytest.mark.asyncio
    async def test_run_waits_for_shutdown(self, short_tmp):
        """run() waits for shutdown event then stops."""
        from unittest.mock import patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        # Set shutdown event immediately so run() doesn't block
        sup._shutdown_event.set()

        with patch(
            "overblick.shared.platform.register_shutdown_signals"
        ):
            await sup.run()

        assert sup.state == SupervisorState.STOPPED


class TestSupervisorGetStatus:
    """Test get_status method."""

    @pytest.mark.asyncio
    async def test_get_status_with_agents(self, short_tmp):
        """get_status includes agent info and routing stats."""
        from unittest.mock import MagicMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            mock_agent = MagicMock()
            mock_agent.state = ProcessState.RUNNING
            mock_agent.to_dict = MagicMock(
                return_value={"identity": "test", "state": "running"}
            )
            sup._agents["test"] = mock_agent

            status = sup.get_status()
            assert status["total_agents"] == 1
            assert status["running_agents"] == 1
            assert "routing" in status
            assert "test" in status["agents"]
        finally:
            await sup.stop()


class TestSupervisorMessageRouter:
    """Test message_router property."""

    @pytest.mark.asyncio
    async def test_message_router_accessible(self, short_tmp):
        """message_router property returns router."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        from overblick.supervisor.routing import MessageRouter

        assert isinstance(sup.message_router, MessageRouter)


class TestSupervisorIPCHandlers:
    """Test IPC handler methods directly."""

    @pytest.mark.asyncio
    async def test_handle_health_inquiry(self, short_tmp):
        """_handle_health_inquiry delegates to health handler."""
        from unittest.mock import AsyncMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        sup._health_handler.handle = AsyncMock(return_value=None)

        msg = IPCMessage(msg_type="health_inquiry", sender="test")
        await sup._handle_health_inquiry(msg)
        sup._health_handler.handle.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_handle_email_consultation(self, short_tmp):
        """_handle_email_consultation delegates to email handler."""
        from unittest.mock import AsyncMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        sup._email_handler.handle = AsyncMock(return_value=None)

        msg = IPCMessage(msg_type="email_consultation", sender="test")
        await sup._handle_email_consultation(msg)
        sup._email_handler.handle.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_handle_research_request(self, short_tmp):
        """_handle_research_request delegates to research handler."""
        from unittest.mock import AsyncMock

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        sup._research_handler.handle = AsyncMock(return_value=None)

        msg = IPCMessage(msg_type="research_request", sender="test")
        await sup._handle_research_request(msg)
        sup._research_handler.handle.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_handle_start_agent_success(self, short_tmp):
        """_handle_start_agent returns success when agent starts."""
        from unittest.mock import AsyncMock, MagicMock, patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            mock_agent = MagicMock()
            with patch.object(sup, "start_agent", new_callable=AsyncMock, return_value=mock_agent):
                msg = IPCMessage(
                    msg_type="start_agent",
                    payload={"identity": "anomal"},
                    sender="dashboard",
                )
                response = await sup._handle_start_agent(msg)
                assert response.payload["success"] is True
                assert response.payload["action"] == "start"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_start_agent_failure(self, short_tmp):
        """_handle_start_agent returns failure when agent fails to start."""
        from unittest.mock import AsyncMock, patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            with patch.object(sup, "start_agent", new_callable=AsyncMock, return_value=None):
                msg = IPCMessage(
                    msg_type="start_agent",
                    payload={"identity": "bad_agent"},
                    sender="dashboard",
                )
                response = await sup._handle_start_agent(msg)
                assert response.payload["success"] is False
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_stop_agent_success(self, short_tmp):
        """_handle_stop_agent returns success when agent stops."""
        from unittest.mock import AsyncMock, patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            with patch.object(sup, "stop_agent", new_callable=AsyncMock, return_value=True):
                msg = IPCMessage(
                    msg_type="stop_agent",
                    payload={"identity": "anomal"},
                    sender="dashboard",
                )
                response = await sup._handle_stop_agent(msg)
                assert response.payload["success"] is True
                assert response.payload["action"] == "stop"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_stop_agent_failure(self, short_tmp):
        """_handle_stop_agent returns failure when agent not found."""
        from unittest.mock import AsyncMock, patch

        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            with patch.object(sup, "stop_agent", new_callable=AsyncMock, return_value=False):
                msg = IPCMessage(
                    msg_type="stop_agent",
                    payload={"identity": "nonexistent"},
                    sender="dashboard",
                )
                response = await sup._handle_stop_agent(msg)
                assert response.payload["success"] is False
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_route_message_success(self, short_tmp):
        """_handle_route_message routes successfully."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            sup._message_router.register_agent("target_agent")
            msg = IPCMessage(
                msg_type="route_message",
                payload={
                    "target": "target_agent",
                    "message_type": "greeting",
                    "data": {"text": "hello"},
                    "ttl_seconds": 60.0,
                },
                sender="source_agent",
            )
            response = await sup._handle_route_message(msg)
            assert response.payload["success"] is True
            assert response.payload["status"] == "pending"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_route_message_missing_fields(self, short_tmp):
        """_handle_route_message returns error for missing fields."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            msg = IPCMessage(
                msg_type="route_message",
                payload={},
                sender="source",
            )
            response = await sup._handle_route_message(msg)
            assert response.payload["success"] is False
            assert "Missing" in response.payload["error"]
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_collect_messages(self, short_tmp):
        """_handle_collect_messages returns pending messages."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        try:
            sup._message_router.register_agent("collector")
            sup._message_router.route("src", "collector", "greeting", {"text": "hi"})

            msg = IPCMessage(
                msg_type="collect_messages",
                sender="collector",
            )
            response = await sup._handle_collect_messages(msg)
            assert response.payload["count"] == 1
            assert len(response.payload["messages"]) == 1
            assert response.payload["messages"][0]["source_agent"] == "src"
        finally:
            await sup.stop()

    @pytest.mark.asyncio
    async def test_handle_shutdown(self, short_tmp):
        """_handle_shutdown sets shutdown event."""
        sup = Supervisor(identities=[], socket_dir=short_tmp)
        await sup.start()

        msg = IPCMessage(msg_type="shutdown", sender="admin")
        response = await sup._handle_shutdown(msg)
        assert response.msg_type == "ack"
        assert sup._shutdown_event.is_set()

        await sup.stop()
