"""
Tests for the Supervisor, AgentProcess, and IPC system.
"""

import asyncio
import json
import shutil
import tempfile
import pytest
from pathlib import Path

from blick.supervisor.supervisor import Supervisor, SupervisorState
from blick.supervisor.process import AgentProcess, ProcessState
from blick.supervisor.ipc import IPCServer, IPCClient, IPCMessage


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
            resource="moltbook.comment", action="write",
            reason="Wants to comment", sender="anomal",
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
                resource="moltbook.comment", action="write",
                reason="test", sender="anomal",
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
            client = IPCClient(target="supervisor", socket_dir=short_tmp)
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
            client = IPCClient(target="supervisor", socket_dir=short_tmp)
            granted = await client.request_permission(
                resource="moltbook.comment", action="write",
                reason="test comment", sender="anomal",
            )
            assert granted is True
        finally:
            await sup.stop()


# ---------------------------------------------------------------------------
# IPC Authentication
# ---------------------------------------------------------------------------

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
