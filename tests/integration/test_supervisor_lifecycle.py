"""
Integration tests — Supervisor lifecycle: IPC + audit.

Tests the full flow: start server → client connects → exchange messages → audit.
Uses real IPC server/client (no mocks) with short temp paths.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from overblick.supervisor.ipc import (
    IPCServer,
    IPCClient,
    IPCMessage,
    generate_ipc_token,
)


@pytest.fixture
def ipc_dir():
    """Short temp dir for Unix sockets."""
    d = Path(tempfile.mkdtemp(prefix="ipc", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestSupervisorIPCLifecycle:
    """Full supervisor IPC lifecycle: start → communicate → stop."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, ipc_dir):
        """Start server, send status request, get response, stop."""
        token = generate_ipc_token()
        server = IPCServer(name="sup", socket_dir=ipc_dir, auth_token=token)

        # Track received messages
        received = []

        async def status_handler(msg: IPCMessage):
            received.append(msg)
            return IPCMessage.status_response(
                status={"agents": 3, "uptime": 42}, sender="supervisor"
            )

        server.on("status_request", status_handler)
        await server.start()

        try:
            client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token=token)
            response = await client.request_status(sender="anomal")

            # Verify server received the message
            assert len(received) == 1
            assert received[0].sender == "anomal"

            # Verify client got the response
            assert response is not None
            assert response["agents"] == 3
            assert response["uptime"] == 42
        finally:
            await server.stop()

        # Verify cleanup
        assert not server.socket_path.exists()
        assert not server.token_path.exists()

    @pytest.mark.asyncio
    async def test_multiple_clients(self, ipc_dir):
        """Multiple agent clients can talk to the same supervisor."""
        token = generate_ipc_token()
        server = IPCServer(name="sup", socket_dir=ipc_dir, auth_token=token)

        senders = []

        async def handler(msg: IPCMessage):
            senders.append(msg.sender)
            return IPCMessage.status_response(
                status={"ok": True}, sender="supervisor"
            )

        server.on("status_request", handler)
        await server.start()

        try:
            for agent_name in ["anomal", "cherry", "blixt"]:
                client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token=token)
                result = await client.request_status(sender=agent_name)
                assert result is not None
                assert result["ok"] is True

            assert senders == ["anomal", "cherry", "blixt"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_permission_flow(self, ipc_dir):
        """Agent requests permission → supervisor grants/denies."""
        token = generate_ipc_token()
        server = IPCServer(name="sup", socket_dir=ipc_dir, auth_token=token)

        async def perm_handler(msg: IPCMessage):
            resource = msg.payload.get("resource", "")
            # Allow moltbook posts, deny telegram
            granted = resource == "moltbook"
            return IPCMessage.permission_response(
                granted=granted,
                reason="policy" if granted else "denied by policy",
            )

        server.on("permission_request", perm_handler)
        await server.start()

        try:
            client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token=token)

            allowed = await client.request_permission(
                resource="moltbook", action="post",
                reason="heartbeat", sender="anomal",
            )
            assert allowed is True

            denied = await client.request_permission(
                resource="telegram", action="send",
                reason="notification", sender="anomal",
            )
            assert denied is False
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_auth_rejection_still_allows_valid(self, ipc_dir):
        """After rejecting bad auth, server still accepts valid messages."""
        token = generate_ipc_token()
        server = IPCServer(name="sup", socket_dir=ipc_dir, auth_token=token)

        async def handler(msg: IPCMessage):
            return IPCMessage.status_response(
                status={"ok": True}, sender="supervisor"
            )

        server.on("status_request", handler)
        await server.start()

        try:
            # Bad client (wrong token)
            bad_client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token="wrong")
            result = await bad_client.send(
                IPCMessage.status_request(sender="evil")
            )
            assert result is None  # Rejected
            assert server.rejected_count == 1

            # Good client (correct token)
            good_client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token=token)
            result = await good_client.request_status(sender="anomal")
            assert result is not None
            assert result["ok"] is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_concurrent_clients(self, ipc_dir):
        """Multiple simultaneous connections don't crash the server."""
        token = generate_ipc_token()
        server = IPCServer(name="sup", socket_dir=ipc_dir, auth_token=token)
        count = 0

        async def handler(msg: IPCMessage):
            nonlocal count
            count += 1
            return IPCMessage.status_response(
                status={"count": count}, sender="supervisor"
            )

        server.on("status_request", handler)
        await server.start()

        try:
            async def send_request(name: str):
                client = IPCClient(target="sup", socket_dir=ipc_dir, auth_token=token)
                return await client.request_status(sender=name)

            results = await asyncio.gather(*[
                send_request(f"agent-{i}") for i in range(10)
            ])

            # All should have gotten responses
            assert all(r is not None for r in results)
            assert count == 10
        finally:
            await server.stop()
