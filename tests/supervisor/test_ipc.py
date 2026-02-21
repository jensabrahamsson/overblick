"""
Tests for IPC — Inter-Process Communication module.

Covers:
- IPCMessage serialization/deserialization and factory methods
- IPCServer start/stop, auth validation, message handling
- IPCClient send/receive, auth injection, timeout handling
- Token file management and permissions
- Message size limits
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from overblick.supervisor.ipc import (
    IPCMessage,
    IPCServer,
    IPCClient,
    generate_ipc_token,
    _MAX_MESSAGE_SIZE,
)


@pytest.fixture
def ipc_dir(request):
    """Short temp dir for Unix sockets (macOS AF_UNIX path limit ~104 chars)."""
    d = Path(tempfile.mkdtemp(prefix="ipc", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestGenerateToken:
    """Tests for token generation."""

    def test_generates_hex_string(self):
        token = generate_ipc_token()
        assert isinstance(token, str)
        # 32 bytes = 64 hex chars
        assert len(token) == 64
        int(token, 16)  # Should be valid hex

    def test_unique_tokens(self):
        tokens = {generate_ipc_token() for _ in range(100)}
        assert len(tokens) == 100


class TestIPCMessage:
    """Tests for IPCMessage serialization and factory methods."""

    def test_roundtrip_serialization(self):
        msg = IPCMessage(
            msg_type="test",
            payload={"key": "value"},
            sender="agent1",
            auth_token="secret123",
        )
        json_str = msg.to_json()
        restored = IPCMessage.from_json(json_str)
        assert restored.msg_type == "test"
        assert restored.payload == {"key": "value"}
        assert restored.sender == "agent1"
        assert restored.auth_token == "secret123"

    def test_from_json_missing_optional_fields(self):
        data = json.dumps({"type": "ping"})
        msg = IPCMessage.from_json(data)
        assert msg.msg_type == "ping"
        assert msg.payload == {}
        assert msg.sender == ""
        assert msg.auth_token == ""

    def test_status_request_factory(self):
        msg = IPCMessage.status_request(sender="anomal", auth_token="tok")
        assert msg.msg_type == "status_request"
        assert msg.sender == "anomal"
        assert msg.auth_token == "tok"

    def test_status_response_factory(self):
        msg = IPCMessage.status_response(
            status={"state": "running"}, sender="supervisor"
        )
        assert msg.msg_type == "status_response"
        assert msg.payload == {"state": "running"}

    def test_permission_request_factory(self):
        msg = IPCMessage.permission_request(
            resource="moltbook", action="post", reason="heartbeat",
            sender="anomal", auth_token="tok",
        )
        assert msg.msg_type == "permission_request"
        assert msg.payload["resource"] == "moltbook"
        assert msg.payload["action"] == "post"
        assert msg.payload["reason"] == "heartbeat"

    def test_permission_response_factory(self):
        msg = IPCMessage.permission_response(granted=True, reason="auto-approved")
        assert msg.msg_type == "permission_response"
        assert msg.payload["granted"] is True

    def test_shutdown_factory(self):
        msg = IPCMessage.shutdown(sender="supervisor", auth_token="tok")
        assert msg.msg_type == "shutdown"

    def test_timestamp_auto_generated(self):
        msg = IPCMessage(msg_type="test")
        assert msg.timestamp  # Should be non-empty ISO timestamp


class TestIPCServer:
    """Tests for IPCServer lifecycle and auth."""

    @pytest_asyncio.fixture
    async def server(self, ipc_dir):
        """Create and start an IPC server in a temp directory."""
        token = generate_ipc_token()
        srv = IPCServer(
            name="test",
            socket_dir=ipc_dir,
            auth_token=token,
        )
        await srv.start()
        yield srv, token
        await srv.stop()

    @pytest.mark.asyncio
    async def test_start_creates_socket(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        assert srv.socket_path.exists()
        await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_removes_socket(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        socket_path = srv.socket_path
        await srv.stop()
        assert not socket_path.exists()

    @pytest.mark.asyncio
    async def test_token_file_created_with_secure_permissions(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="secrettoken")
        await srv.start()
        token_path = srv.token_path
        assert token_path.exists()
        assert token_path.read_text() == "secrettoken"
        # Check permissions (owner-only)
        mode = oct(os.stat(str(token_path)).st_mode)[-3:]
        assert mode == "600"
        await srv.stop()

    @pytest.mark.asyncio
    async def test_token_file_cleaned_up_on_stop(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        assert srv.token_path.exists()
        await srv.stop()
        assert not srv.token_path.exists()

    @pytest.mark.asyncio
    async def test_no_token_file_when_auth_disabled(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="")
        await srv.start()
        assert not srv.token_path.exists()
        await srv.stop()

    @pytest.mark.asyncio
    async def test_stale_socket_removed_on_start(self, ipc_dir):
        socket_path = ipc_dir / "overblick-test.sock"
        socket_path.write_text("stale")
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        # Should have replaced the stale file with a real socket
        assert srv.socket_path.exists()
        await srv.stop()

    @pytest.mark.asyncio
    async def test_auth_validation_accepts_correct_token(self, server):
        srv, token = server
        msg = IPCMessage(msg_type="test", auth_token=token)
        assert srv._validate_auth(msg) is True

    @pytest.mark.asyncio
    async def test_auth_validation_rejects_wrong_token(self, server):
        srv, _token = server
        msg = IPCMessage(msg_type="test", auth_token="wrong-token")
        assert srv._validate_auth(msg) is False

    @pytest.mark.asyncio
    async def test_auth_validation_accepts_any_when_disabled(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="")
        msg = IPCMessage(msg_type="test", auth_token="anything")
        assert srv._validate_auth(msg) is True

    @pytest.mark.asyncio
    async def test_rejected_count_increments(self, server):
        srv, _token = server
        assert srv.rejected_count == 0
        # Send a message with wrong token
        client = IPCClient(
            target="test", socket_dir=srv._socket_dir, auth_token="bad-token",
        )
        await client.send(IPCMessage(msg_type="test"))
        # Small delay for server to process
        await asyncio.sleep(0.1)
        assert srv.rejected_count == 1


class TestIPCClientServer:
    """Integration tests for IPC client-server communication."""

    @pytest.mark.asyncio
    async def test_send_receive_with_handler(self, ipc_dir):
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)

        async def status_handler(msg: IPCMessage):
            return IPCMessage.status_response(
                status={"state": "running"}, sender="supervisor"
            )

        srv.on("status_request", status_handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            response = await client.send(
                IPCMessage.status_request(sender="anomal", auth_token=token)
            )
            assert response is not None
            assert response.msg_type == "status_response"
            assert response.payload["state"] == "running"
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_auth_token_injected_by_client(self, ipc_dir):
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)

        received_tokens = []

        async def handler(msg: IPCMessage):
            received_tokens.append(msg.auth_token)
            return None

        srv.on("ping", handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            # Send without explicit auth_token — client should inject it
            msg = IPCMessage(msg_type="ping", sender="anomal")
            await client.send(msg)
            await asyncio.sleep(0.1)
            assert len(received_tokens) == 1
            assert received_tokens[0] == token
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_request_status_helper(self, ipc_dir):
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)

        async def status_handler(msg: IPCMessage):
            return IPCMessage.status_response(
                status={"agents": 3, "uptime": 120},
                sender="supervisor",
            )

        srv.on("status_request", status_handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            result = await client.request_status(sender="test-agent")
            assert result is not None
            assert result["agents"] == 3
            assert result["uptime"] == 120
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_request_permission_helper(self, ipc_dir):
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)

        async def perm_handler(msg: IPCMessage):
            return IPCMessage.permission_response(
                granted=True, reason="auto-approved"
            )

        srv.on("permission_request", perm_handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            granted = await client.request_permission(
                resource="moltbook", action="post",
                reason="heartbeat", sender="anomal",
            )
            assert granted is True
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_client_socket_not_found(self, ipc_dir):
        client = IPCClient(target="nonexistent", socket_dir=ipc_dir, auth_token="tok")
        result = await client.send(IPCMessage(msg_type="ping"))
        assert result is None

    @pytest.mark.asyncio
    async def test_handler_not_registered(self, ipc_dir):
        """Message with no handler is logged but doesn't crash."""
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            result = await client.send(
                IPCMessage(msg_type="unknown_type", auth_token=token)
            )
            # No handler means no response
            assert result is None
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_no_response_from_handler(self, ipc_dir):
        """Handler returning None means no response sent."""
        token = generate_ipc_token()
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token=token)

        async def silent_handler(msg: IPCMessage):
            return None  # No response

        srv.on("fire_and_forget", silent_handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            result = await client.send(
                IPCMessage(msg_type="fire_and_forget", auth_token=token),
                timeout=1.0,
            )
            assert result is None
        finally:
            await srv.stop()
