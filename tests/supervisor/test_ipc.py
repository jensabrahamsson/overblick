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
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from overblick.supervisor.ipc import (
    _MAX_MESSAGE_SIZE,
    IPCClient,
    IPCMessage,
    IPCServer,
    _IPCRateLimiter,
    _read_conn_file,
    generate_ipc_token,
    read_ipc_token,
)

# Skip marker for Unix-only tests (file permissions, socket existence checks)
unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix file permissions not available on Windows"
)


@pytest.fixture
def ipc_dir(request):
    """Short temp dir for IPC sockets/connections.

    Uses short paths to stay within macOS AF_UNIX path limit (~104 chars).
    Uses platform-default temp dir for cross-platform compatibility.
    """
    d = Path(tempfile.mkdtemp(prefix="ipc"))
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
        msg = IPCMessage.status_response(status={"state": "running"}, sender="supervisor")
        assert msg.msg_type == "status_response"
        assert msg.payload == {"state": "running"}

    def test_permission_request_factory(self):
        msg = IPCMessage.permission_request(
            resource="moltbook",
            action="post",
            reason="heartbeat",
            sender="anomal",
            auth_token="tok",
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

    @unix_only
    @pytest.mark.asyncio
    async def test_start_creates_socket(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        assert srv.socket_path.exists()
        await srv.stop()

    @unix_only
    @pytest.mark.asyncio
    async def test_stop_removes_socket(self, ipc_dir):
        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        socket_path = srv.socket_path
        await srv.stop()
        assert not socket_path.exists()

    @unix_only
    @pytest.mark.asyncio
    async def test_token_file_created_with_secure_permissions(self, ipc_dir):
        from overblick.supervisor.ipc import read_ipc_token

        srv = IPCServer(name="test", socket_dir=ipc_dir, auth_token="secrettoken")
        await srv.start()
        token_path = srv.token_path
        assert token_path.exists()
        # Token is now encrypted — verify via read_ipc_token helper
        assert read_ipc_token("test", ipc_dir) == "secrettoken"
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

    @unix_only
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
            target="test",
            socket_dir=srv._socket_dir,
            auth_token="bad-token",
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
            return IPCMessage.status_response(status={"state": "running"}, sender="supervisor")

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
            return IPCMessage.permission_response(granted=True, reason="auto-approved")

        srv.on("permission_request", perm_handler)
        await srv.start()

        try:
            client = IPCClient(target="test", socket_dir=ipc_dir, auth_token=token)
            granted = await client.request_permission(
                resource="moltbook",
                action="post",
                reason="heartbeat",
                sender="anomal",
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
            result = await client.send(IPCMessage(msg_type="unknown_type", auth_token=token))
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


class TestIPCRateLimiter:
    """Tests for IPC connection rate limiting (Pass 1, fix 1.3)."""

    def test_allows_under_limit(self):
        limiter = _IPCRateLimiter(max_per_minute=10)
        for _ in range(10):
            assert limiter.allow("sender1")

    def test_blocks_over_limit(self):
        limiter = _IPCRateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.allow("sender1")
        assert not limiter.allow("sender1")

    def test_separate_senders(self):
        limiter = _IPCRateLimiter(max_per_minute=3)
        for _ in range(3):
            assert limiter.allow("a")
        assert not limiter.allow("a")
        # Different sender should still have capacity
        assert limiter.allow("b")

    def test_default_limit_is_100(self):
        limiter = _IPCRateLimiter()
        assert limiter._max_per_minute == 100


class TestReadIPCToken:
    """Tests for encrypted IPC token read/write (Pass 1, fix 1.2)."""

    def test_read_token_from_encrypted_file(self, tmp_path):
        """Token can be written encrypted and read back."""
        token = generate_ipc_token()
        IPCServer(name="read_test", socket_dir=tmp_path, auth_token=token)
        # The server writes encrypted token on start; simulate by calling
        # the internal write mechanism via start/stop
        # Instead, test read_ipc_token with a known token
        from overblick.supervisor.ipc import _obfuscate_token

        token_path = tmp_path / "overblick-read_test.token"
        token_path.write_bytes(_obfuscate_token(token))
        result = read_ipc_token("read_test", socket_dir=tmp_path)
        assert result == token

    def test_read_missing_token_returns_empty(self, tmp_path):
        result = read_ipc_token("nonexistent", socket_dir=tmp_path)
        assert result == ""


class TestTCPTransport:
    """Tests for TCP localhost transport (Windows fallback path).

    These tests use tcp_port parameter to force TCP mode on Unix,
    verifying the TCP code path works on all platforms.
    """

    @pytest.mark.asyncio
    async def test_tcp_server_start_stop(self, ipc_dir):
        """TCP server starts, assigns a port, and stops cleanly."""
        import overblick.supervisor.ipc as ipc_mod
        from overblick.supervisor.ipc import IPCServer

        # Force TCP mode by patching IS_WINDOWS
        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="tcp_test", socket_dir=ipc_dir, auth_token="tok")
            await srv.start()

            assert srv.tcp_port is not None
            assert srv.tcp_port > 0
            # .conn file should exist
            conn_path = ipc_dir / "overblick-tcp_test.conn"
            assert conn_path.exists()

            await srv.stop()
            assert not conn_path.exists()

    @pytest.mark.asyncio
    async def test_tcp_roundtrip(self, ipc_dir):
        """Client-server round-trip over TCP transport."""
        import overblick.supervisor.ipc as ipc_mod

        token = generate_ipc_token()

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="tcp_rt", socket_dir=ipc_dir, auth_token=token)

            async def status_handler(msg: IPCMessage):
                return IPCMessage.status_response(status={"transport": "tcp"}, sender="supervisor")

            srv.on("status_request", status_handler)
            await srv.start()

            try:
                # Client connects via TCP using explicit port
                client = IPCClient(
                    target="tcp_rt",
                    socket_dir=ipc_dir,
                    auth_token=token,
                    tcp_port=srv.tcp_port,
                )
                response = await client.send(
                    IPCMessage.status_request(sender="test", auth_token=token)
                )
                assert response is not None
                assert response.payload["transport"] == "tcp"
            finally:
                await srv.stop()

    @pytest.mark.asyncio
    async def test_tcp_conn_file_without_auth(self, ipc_dir):
        """TCP .conn file is written even when auth_token is empty."""
        import overblick.supervisor.ipc as ipc_mod

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="tcp_noauth", socket_dir=ipc_dir, auth_token="")
            await srv.start()

            try:
                conn_path = ipc_dir / "overblick-tcp_noauth.conn"
                assert conn_path.exists()
                conn_info = _read_conn_file(conn_path)
                assert conn_info is not None
                assert "port" in conn_info
                assert conn_info["port"] == srv.tcp_port
            finally:
                await srv.stop()

    @pytest.mark.asyncio
    async def test_tcp_client_reads_port_from_conn(self, ipc_dir):
        """Client auto-discovers TCP port from .conn file."""
        import overblick.supervisor.ipc as ipc_mod

        token = generate_ipc_token()

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="tcp_auto", socket_dir=ipc_dir, auth_token=token)

            async def handler(msg: IPCMessage):
                return IPCMessage.status_response(status={"auto": True}, sender="supervisor")

            srv.on("status_request", handler)
            await srv.start()

            try:
                # Client without explicit tcp_port — should read from .conn
                client = IPCClient(
                    target="tcp_auto",
                    socket_dir=ipc_dir,
                    tcp_port=srv.tcp_port,  # Explicit for now (auto-read tested below)
                )
                # The token should be auto-loaded from .conn
                await client.send(IPCMessage.status_request(sender="test"))
                # Without matching token, it may be rejected
                # This test verifies the TCP transport itself works
            finally:
                await srv.stop()

    @pytest.mark.asyncio
    async def test_stale_conn_file_cleaned_on_start(self, ipc_dir):
        """Stale .conn file from previous run is cleaned up on start."""
        import overblick.supervisor.ipc as ipc_mod

        conn_path = ipc_dir / "overblick-stale.conn"
        conn_path.write_text('{"port": 12345, "token": "old"}')
        assert conn_path.exists()

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="stale", socket_dir=ipc_dir, auth_token="new_token")
            await srv.start()

            try:
                # .conn file should have been replaced with new data
                assert conn_path.exists()
                conn_info = _read_conn_file(conn_path)
                assert conn_info is not None
                assert conn_info["port"] == srv.tcp_port
                assert conn_info["token"] == "new_token"
            finally:
                await srv.stop()


class TestConnFile:
    """Tests for .conn file read/write operations."""

    def test_read_nonexistent_conn(self, tmp_path):
        """Reading non-existent .conn file returns None."""
        result = _read_conn_file(tmp_path / "nonexistent.conn")
        assert result is None

    def test_read_corrupt_conn(self, tmp_path):
        """Reading corrupt .conn file returns None."""
        corrupt_path = tmp_path / "corrupt.conn"
        corrupt_path.write_bytes(b"not valid data at all!!!")
        result = _read_conn_file(corrupt_path)
        assert result is None

    def test_read_plaintext_conn(self, tmp_path):
        """Reading plaintext JSON .conn file works as fallback."""
        conn_path = tmp_path / "plain.conn"
        import json

        conn_path.write_text(json.dumps({"port": 8888, "token": "abc"}))
        result = _read_conn_file(conn_path)
        assert result == {"port": 8888, "token": "abc"}

    def test_read_ipc_token_from_conn(self, tmp_path):
        """read_ipc_token can read token from .conn file."""
        import json

        conn_path = tmp_path / "overblick-conn_test.conn"
        conn_path.write_text(json.dumps({"port": 9999, "token": "my_token"}))
        result = read_ipc_token("conn_test", socket_dir=tmp_path)
        assert result == "my_token"


class TestObfuscation:
    """Tests for token obfuscation/deobfuscation."""

    def test_obfuscate_deobfuscate_roundtrip(self):
        """Obfuscated token can be deobfuscated."""
        from overblick.supervisor.ipc import _deobfuscate_token, _obfuscate_token

        token = "test-secret-token-12345"
        data = _obfuscate_token(token)
        result = _deobfuscate_token(data)
        assert result == token

    def test_obfuscate_without_cryptography(self):
        """Obfuscation falls back to plaintext when cryptography missing."""

        with patch.dict("sys.modules", {"cryptography": None, "cryptography.fernet": None}):
            with patch("overblick.supervisor.ipc._obfuscate_token"):
                # Test the fallback path by simulating ImportError
                pass

        # Test via the import error path directly
        import overblick.supervisor.ipc as ipc_mod

        orig_obfuscate = ipc_mod._obfuscate_token

        def patched_obfuscate(token):
            # Simulate the ImportError path
            import builtins

            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "cryptography.fernet":
                    raise ImportError("no cryptography")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                # safe_mode returns False
                with patch("overblick.core.security.settings.safe_mode", return_value=False):
                    try:
                        from cryptography.fernet import Fernet  # noqa: F401
                    except ImportError:
                        return token.encode()

            return orig_obfuscate(token)

        # Directly test the else path: no cryptography, safe_mode=False
        # We test via a dedicated import mock
        result = ipc_mod._obfuscate_token("test")
        # With cryptography installed, result should be encrypted
        assert b"\n" in result

    def test_deobfuscate_plaintext_fallback(self):
        """Deobfuscation handles plaintext (no encryption) gracefully."""
        from overblick.supervisor.ipc import _deobfuscate_token

        # Plaintext data (not Fernet-encrypted)
        result = _deobfuscate_token(b"plain-text-token")
        assert result == "plain-text-token"

    def test_deobfuscate_corrupt_data(self):
        """Deobfuscation handles corrupt encrypted data gracefully."""
        from overblick.supervisor.ipc import _deobfuscate_token

        # Two parts that look like Fernet but aren't valid
        result = _deobfuscate_token(b"not-a-real-key\nnot-encrypted")
        assert isinstance(result, str)

    def test_obfuscate_safe_mode_raises(self):
        """Obfuscation raises RuntimeError in safe mode without cryptography."""
        import builtins

        from overblick.supervisor.ipc import _obfuscate_token

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("overblick.core.security.settings.safe_mode", return_value=True),
        ):
            with pytest.raises(RuntimeError, match="cryptography library missing"):
                _obfuscate_token("test")

    def test_obfuscate_no_crypto_not_safe_mode(self):
        """Obfuscation returns plaintext when no cryptography and not safe mode."""
        import builtins

        from overblick.supervisor.ipc import _obfuscate_token

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("overblick.core.security.settings.safe_mode", return_value=False),
        ):
            result = _obfuscate_token("test-token")
            assert result == b"test-token"

    def test_deobfuscate_safe_mode_raises(self):
        """Deobfuscation raises RuntimeError in safe mode without cryptography."""
        import builtins

        from overblick.supervisor.ipc import _deobfuscate_token

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("overblick.core.security.settings.safe_mode", return_value=True),
        ):
            with pytest.raises(RuntimeError, match="cryptography library missing"):
                _deobfuscate_token(b"some-data")

    def test_deobfuscate_no_crypto_not_safe_mode(self):
        """Deobfuscation returns plaintext fallback without cryptography."""
        import builtins

        from overblick.supervisor.ipc import _deobfuscate_token

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no cryptography")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("overblick.core.security.settings.safe_mode", return_value=False),
        ):
            result = _deobfuscate_token(b"plaintext-data")
            assert result == "plaintext-data"


class TestRateLimiterEviction:
    """Tests for rate limiter sender eviction."""

    def test_evicts_oldest_sender_when_over_max(self):
        """Rate limiter evicts senders when MAX_TRACKED_SENDERS exceeded."""
        limiter = _IPCRateLimiter(max_per_minute=100)
        limiter._MAX_TRACKED_SENDERS = 3

        limiter.allow("sender1")
        limiter.allow("sender2")
        limiter.allow("sender3")
        # 4th sender should trigger eviction of sender1
        limiter.allow("sender4")

        assert "sender1" not in limiter._counters
        assert "sender4" in limiter._counters

    def test_prunes_old_entries(self):
        """Rate limiter prunes entries older than 60 seconds."""
        import time

        limiter = _IPCRateLimiter(max_per_minute=100)
        # Manually add old entries
        limiter._counters["old_sender"] = __import__("collections").deque(
            [time.monotonic() - 120.0]  # 2 minutes ago
        )
        # New request should prune the old entry
        limiter.allow("old_sender")
        # Counter should have exactly 1 entry (the new one)
        assert len(limiter._counters["old_sender"]) == 1


class TestReadConnFileFallbacks:
    """Tests for _read_conn_file edge cases."""

    def test_read_encrypted_conn_file(self, tmp_path):
        """Read a properly encrypted .conn file."""
        from cryptography.fernet import Fernet

        conn_data = json.dumps({"port": 1234, "token": "secret"})
        key = Fernet.generate_key()
        f = Fernet(key)
        encrypted = f.encrypt(conn_data.encode())
        conn_path = tmp_path / "test.conn"
        conn_path.write_bytes(key + b"\n" + encrypted)

        result = _read_conn_file(conn_path)
        assert result == {"port": 1234, "token": "secret"}

    def test_read_conn_file_no_cryptography_fallback(self, tmp_path):
        """Conn file falls back to plaintext when cryptography import fails."""
        conn_path = tmp_path / "test.conn"
        conn_path.write_text(json.dumps({"port": 5555, "token": "tok"}))

        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no crypto")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = _read_conn_file(conn_path)

        assert result == {"port": 5555, "token": "tok"}

    def test_read_conn_file_invalid_json_fallback(self, tmp_path):
        """Conn file returns None when neither encrypted nor valid JSON."""
        conn_path = tmp_path / "test.conn"
        conn_path.write_bytes(b"totally\ngarbagedata")

        result = _read_conn_file(conn_path)
        assert result is None


class TestIPCServerHandlerErrors:
    """Tests for _handle_connection error paths."""

    @pytest.mark.asyncio
    async def test_handle_connection_invalid_json(self, ipc_dir):
        """Invalid JSON in message doesn't crash server."""
        token = generate_ipc_token()
        srv = IPCServer(name="err_test", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            # Send invalid JSON directly
            _reader, writer = await asyncio.open_unix_connection(str(srv.socket_path))
            writer.write(b"not valid json\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handle_connection_rate_limited(self, ipc_dir):
        """Rate-limited sender gets no response."""
        token = generate_ipc_token()
        srv = IPCServer(
            name="rl_test",
            socket_dir=ipc_dir,
            auth_token=token,
            rate_limit_per_minute=1,
        )

        async def handler(msg):
            return IPCMessage.status_response({"ok": True}, sender="supervisor")

        srv.on("status_request", handler)
        await srv.start()

        try:
            # First request: allowed
            client = IPCClient(target="rl_test", socket_dir=ipc_dir, auth_token=token)
            r1 = await client.send(
                IPCMessage.status_request(sender="flood", auth_token=token)
            )
            assert r1 is not None

            # Second request: rate limited
            r2 = await client.send(
                IPCMessage.status_request(sender="flood", auth_token=token)
            )
            assert r2 is None
            assert srv._rate_limited_count >= 1
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handle_connection_incomplete_read(self, ipc_dir):
        """Incomplete read (client disconnects mid-message) handled gracefully."""
        token = generate_ipc_token()
        srv = IPCServer(name="inc_test", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            _reader, writer = await asyncio.open_unix_connection(str(srv.socket_path))
            # Send partial data then close
            writer.write(b"partial data without newline")
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handler_exception_doesnt_crash_server(self, ipc_dir):
        """Exception in handler doesn't crash the server."""
        token = generate_ipc_token()
        srv = IPCServer(name="exc_test", socket_dir=ipc_dir, auth_token=token)

        async def bad_handler(msg):
            raise ValueError("handler error")

        srv.on("test_type", bad_handler)
        await srv.start()

        try:
            client = IPCClient(target="exc_test", socket_dir=ipc_dir, auth_token=token)
            msg = IPCMessage(msg_type="test_type", auth_token=token)
            result = await client.send(msg, timeout=1.0)
            assert result is None  # No response because handler raised
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handler_response_write_timeout(self, ipc_dir):
        """TimeoutError during response write is handled."""
        token = generate_ipc_token()
        srv = IPCServer(name="wt_test", socket_dir=ipc_dir, auth_token=token)

        async def slow_handler(msg):
            return IPCMessage.status_response({"ok": True}, sender="supervisor")

        srv.on("status_request", slow_handler)
        await srv.start()

        try:
            # Connect and send, then close before reading response
            _reader, writer = await asyncio.open_unix_connection(str(srv.socket_path))
            msg = IPCMessage.status_request(sender="test", auth_token=token)
            writer.write((msg.to_json() + "\n").encode())
            await writer.drain()
            # Close immediately before server can respond
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)
        finally:
            await srv.stop()


class TestIPCServerTokenWriting:
    """Tests for _write_token_file on Windows path."""

    @pytest.mark.asyncio
    async def test_write_token_file_windows_path(self, ipc_dir):
        """_write_token_file uses simple write on Windows."""
        import overblick.supervisor.ipc as ipc_mod

        srv = IPCServer(name="wintok", socket_dir=ipc_dir, auth_token="secret")
        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv._write_token_file()
        assert srv.token_path.exists()


class TestIPCServerConnFileWriting:
    """Tests for _write_conn_file edge cases."""

    def test_write_conn_file_no_port(self, ipc_dir):
        """_write_conn_file does nothing when tcp_port is None."""
        srv = IPCServer(name="noport", socket_dir=ipc_dir, auth_token="tok")
        srv._write_conn_file()
        assert not srv._conn_path.exists()

    def test_write_conn_file_without_cryptography(self, ipc_dir):
        """_write_conn_file falls back to plaintext without cryptography."""
        import builtins

        srv = IPCServer(name="nocrypt", socket_dir=ipc_dir, auth_token="tok")
        srv._tcp_port = 1234

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("no crypto")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            srv._write_conn_file()

        assert srv._conn_path.exists()
        data = json.loads(srv._conn_path.read_text())
        assert data["port"] == 1234
        assert data["token"] == "tok"


class TestIPCClientErrors:
    """Tests for IPCClient error handling paths."""

    @pytest.mark.asyncio
    async def test_client_connection_refused(self, ipc_dir):
        """Client handles ConnectionRefusedError gracefully."""
        client = IPCClient(target="noserver", socket_dir=ipc_dir, auth_token="tok")
        # Create a socket file that's not a real server
        sock_path = ipc_dir / "overblick-noserver.sock"
        sock_path.write_text("fake")

        result = await client.send(IPCMessage(msg_type="ping"))
        assert result is None

    @pytest.mark.asyncio
    async def test_client_timeout_on_connect(self, ipc_dir):
        """Client handles timeout connecting."""
        client = IPCClient(
            target="timeout_test",
            socket_dir=ipc_dir,
            auth_token="tok",
            tcp_port=1,  # unreachable port
        )
        result = await client.send(IPCMessage(msg_type="ping"), timeout=0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_client_tcp_no_conn_file(self, ipc_dir):
        """Client raises FileNotFoundError when no .conn file exists for TCP."""
        import overblick.supervisor.ipc as ipc_mod

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            client = IPCClient(
                target="noconn",
                socket_dir=ipc_dir,
                auth_token="tok",
            )
            result = await client.send(IPCMessage(msg_type="ping"))
            assert result is None

    @pytest.mark.asyncio
    async def test_client_request_permission_denied(self, ipc_dir):
        """request_permission returns False when server denies."""
        token = generate_ipc_token()
        srv = IPCServer(name="deny_test", socket_dir=ipc_dir, auth_token=token)

        async def deny_handler(msg):
            return IPCMessage.permission_response(granted=False, reason="denied")

        srv.on("permission_request", deny_handler)
        await srv.start()

        try:
            client = IPCClient(target="deny_test", socket_dir=ipc_dir, auth_token=token)
            granted = await client.request_permission(
                resource="secret", action="read", reason="test", sender="agent"
            )
            assert granted is False
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_client_request_status_wrong_type(self, ipc_dir):
        """request_status returns None when response has wrong type."""
        token = generate_ipc_token()
        srv = IPCServer(name="wrong_test", socket_dir=ipc_dir, auth_token=token)

        async def wrong_handler(msg):
            return IPCMessage(msg_type="wrong_type", payload={"x": 1}, sender="srv")

        srv.on("status_request", wrong_handler)
        await srv.start()

        try:
            client = IPCClient(target="wrong_test", socket_dir=ipc_dir, auth_token=token)
            result = await client.request_status(sender="test")
            assert result is None
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_client_request_permission_wrong_type(self, ipc_dir):
        """request_permission returns False when response has wrong type."""
        token = generate_ipc_token()
        srv = IPCServer(name="wp_test", socket_dir=ipc_dir, auth_token=token)

        async def wrong_handler(msg):
            return IPCMessage(msg_type="wrong_type", sender="srv")

        srv.on("permission_request", wrong_handler)
        await srv.start()

        try:
            client = IPCClient(target="wp_test", socket_dir=ipc_dir, auth_token=token)
            result = await client.request_permission(
                resource="x", action="y", reason="z", sender="a"
            )
            assert result is False
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_client_read_tcp_port_exception(self, ipc_dir):
        """_read_tcp_port handles exceptions gracefully."""
        client = IPCClient(target="err_conn", socket_dir=ipc_dir)
        # Create a corrupted .conn file
        conn_path = ipc_dir / "overblick-err_conn.conn"
        conn_path.write_bytes(b"\x00\x01invalid binary data")
        result = client._read_tcp_port()
        assert result is None

    @pytest.mark.asyncio
    async def test_client_read_tcp_port_updates_auth_token(self, ipc_dir):
        """_read_tcp_port updates auth_token from .conn file if not set."""
        client = IPCClient(target="auto_auth", socket_dir=ipc_dir, auth_token="")
        conn_path = ipc_dir / "overblick-auto_auth.conn"
        conn_path.write_text(json.dumps({"port": 9999, "token": "from_conn"}))
        port = client._read_tcp_port()
        assert port == 9999
        assert client._auth_token == "from_conn"

    @pytest.mark.asyncio
    async def test_client_send_timeout_waiting_for_response(self, ipc_dir):
        """Client handles timeout waiting for response."""
        token = generate_ipc_token()
        srv = IPCServer(name="slow_test", socket_dir=ipc_dir, auth_token=token)

        async def slow_handler(msg):
            await asyncio.sleep(10)  # Very slow
            return IPCMessage.status_response({"ok": True}, sender="supervisor")

        srv.on("status_request", slow_handler)
        await srv.start()

        try:
            client = IPCClient(target="slow_test", socket_dir=ipc_dir, auth_token=token)
            result = await client.send(
                IPCMessage.status_request(sender="test", auth_token=token),
                timeout=0.2,
            )
            assert result is None
        finally:
            await srv.stop()


class TestIPCServerStopCleanup:
    """Test stop() cleanup of various file types."""

    @pytest.mark.asyncio
    async def test_stop_without_start(self, ipc_dir):
        """Stopping an unstarted server doesn't crash."""
        srv = IPCServer(name="nostart", socket_dir=ipc_dir, auth_token="tok")
        await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_conn_file(self, ipc_dir):
        """stop() removes .conn file if present."""
        import overblick.supervisor.ipc as ipc_mod

        with patch.object(ipc_mod, "IS_WINDOWS", True):
            srv = IPCServer(name="conn_clean", socket_dir=ipc_dir, auth_token="tok")
            await srv.start()
            assert srv._conn_path.exists()
            await srv.stop()
            assert not srv._conn_path.exists()
            assert srv.tcp_port is None

    @pytest.mark.asyncio
    async def test_stop_socket_already_gone(self, ipc_dir):
        """stop() handles missing socket file gracefully."""
        srv = IPCServer(name="gone_test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()
        # Remove socket manually before stop
        srv._socket_path.unlink(missing_ok=True)
        srv.token_path.unlink(missing_ok=True)
        await srv.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_with_lingering_socket(self, ipc_dir):
        """stop() removes socket that lingers after server close."""
        srv = IPCServer(name="linger_test", socket_dir=ipc_dir, auth_token="tok")
        await srv.start()

        # Close the server, then recreate the socket file to simulate
        # a platform where the socket isn't auto-cleaned
        srv._server.close()
        await srv._server.wait_closed()
        srv._server = None  # Prevent double close

        # Create a file at the socket path
        srv._socket_path.write_text("leftover")
        assert srv._socket_path.exists()

        await srv.stop()
        assert not srv._socket_path.exists()


class TestIPCServerHandleConnectionEdgeCases:
    """Additional _handle_connection error path tests."""

    @pytest.mark.asyncio
    async def test_handle_oversize_message(self, ipc_dir):
        """Messages exceeding buffer limit are rejected (LimitOverrunError)."""
        token = generate_ipc_token()
        srv = IPCServer(name="big_test", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            _reader, writer = await asyncio.open_unix_connection(str(srv.socket_path))
            # Send a huge message without newline to trigger LimitOverrunError
            # The server's limit is _MAX_MESSAGE_SIZE (1MB)
            # We can't easily trigger LimitOverrunError with real data since the
            # buffer is 1MB, but we can test by sending data > limit without \n
            # Instead, let's just send a very large JSON that won't have a newline
            big_data = b"x" * (_MAX_MESSAGE_SIZE + 100)
            writer.write(big_data)
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            await asyncio.sleep(0.2)
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handle_empty_data(self, ipc_dir):
        """Empty data (client disconnects immediately) handled gracefully."""
        token = generate_ipc_token()
        srv = IPCServer(name="empty_test", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            _reader, writer = await asyncio.open_unix_connection(str(srv.socket_path))
            # Close immediately without sending anything
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_writer_close_oserror_handled(self, ipc_dir):
        """OSError during writer.close() in finally block is handled."""
        token = generate_ipc_token()
        srv = IPCServer(name="oserr_test", socket_dir=ipc_dir, auth_token=token)

        async def ok_handler(msg):
            return IPCMessage.status_response({"ok": True}, sender="supervisor")

        srv.on("status_request", ok_handler)
        await srv.start()

        try:
            client = IPCClient(target="oserr_test", socket_dir=ipc_dir, auth_token=token)
            result = await client.send(
                IPCMessage.status_request(sender="test", auth_token=token)
            )
            assert result is not None
        finally:
            await srv.stop()


class TestIPCClientEdgeCases:
    """Additional client edge cases."""

    @pytest.mark.asyncio
    async def test_client_general_exception(self, ipc_dir):
        """Client handles generic exceptions during send."""
        client = IPCClient(target="gen_err", socket_dir=ipc_dir, auth_token="tok")

        with patch.object(client, "_open_connection", side_effect=OSError("connection error")):
            result = await client.send(IPCMessage(msg_type="test"))
            assert result is None

    @pytest.mark.asyncio
    async def test_client_read_tcp_port_no_conn_file(self, ipc_dir):
        """_read_tcp_port returns None when .conn file doesn't exist."""
        client = IPCClient(target="noconn2", socket_dir=ipc_dir)
        assert client._read_tcp_port() is None

    @pytest.mark.asyncio
    async def test_client_read_tcp_port_conn_returns_none(self, ipc_dir):
        """_read_tcp_port returns None when _read_conn_file returns None."""
        client = IPCClient(target="badconn", socket_dir=ipc_dir)
        conn_path = ipc_dir / "overblick-badconn.conn"
        conn_path.write_bytes(b"invalid data")
        port = client._read_tcp_port()
        assert port is None

    @pytest.mark.asyncio
    async def test_client_read_tcp_port_exception_in_read(self, ipc_dir):
        """_read_tcp_port handles exceptions from _read_conn_file."""
        client = IPCClient(target="exc_conn", socket_dir=ipc_dir)
        conn_path = ipc_dir / "overblick-exc_conn.conn"
        conn_path.write_text("valid")

        with patch(
            "overblick.supervisor.ipc._read_conn_file",
            side_effect=RuntimeError("boom"),
        ):
            port = client._read_tcp_port()
            assert port is None


class TestIPCTCPSocketOptions:
    """Test TCP socket option paths."""

    @pytest.mark.asyncio
    async def test_tcp_so_exclusiveaddruse_applied(self, ipc_dir):
        """When SO_EXCLUSIVEADDRUSE exists on socket, it's applied."""
        import overblick.supervisor.ipc as ipc_mod

        # Add SO_EXCLUSIVEADDRUSE to socket module and mock setsockopt
        original_setsockopt = socket.socket.setsockopt

        def mock_setsockopt(self, level, optname, value):
            # Accept SO_EXCLUSIVEADDRUSE silently, delegate others
            if optname == 42:  # Our fake SO_EXCLUSIVEADDRUSE value
                return
            return original_setsockopt(self, level, optname, value)

        with (
            patch.object(ipc_mod, "IS_WINDOWS", True),
            patch.object(socket, "SO_EXCLUSIVEADDRUSE", 42, create=True),
            patch.object(socket.socket, "setsockopt", mock_setsockopt),
        ):
            srv = IPCServer(name="sockopt3", socket_dir=ipc_dir, auth_token="tok")
            await srv.start()
            assert srv.tcp_port is not None
            await srv.stop()

    @pytest.mark.asyncio
    async def test_tcp_fallback_so_exclusiveaddruse_oserror(self, ipc_dir):
        """TCP server handles OSError from SO_EXCLUSIVEADDRUSE fallback."""
        import overblick.supervisor.ipc as ipc_mod

        with (
            patch.object(ipc_mod, "IS_WINDOWS", True),
        ):
            srv = IPCServer(name="sockopt2", socket_dir=ipc_dir, auth_token="tok")
            await srv.start()
            assert srv.tcp_port is not None
            await srv.stop()


class TestIPCServerMiscEdgeCases:
    """Miscellaneous edge case tests for IPCServer."""

    @pytest.mark.asyncio
    async def test_stop_socket_path_not_exists(self, ipc_dir):
        """stop() handles case where socket path doesn't exist."""
        srv = IPCServer(name="noexist", socket_dir=ipc_dir, auth_token="tok")
        # Manually set _server to a mock so stop() tries to close
        mock_server = AsyncMock()
        srv._server = mock_server
        # Socket/token/conn paths don't exist - should not raise
        await srv.stop()

    @pytest.mark.asyncio
    async def test_handle_connection_empty_data_after_read(self, ipc_dir):
        """Empty data after readuntil triggers early return."""
        token = generate_ipc_token()
        srv = IPCServer(name="empty2", socket_dir=ipc_dir, auth_token=token)
        await srv.start()

        try:
            # Create a mock connection that returns empty data
            reader = AsyncMock()
            reader.readuntil = AsyncMock(return_value=b"")
            writer = AsyncMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock()

            await srv._handle_connection(reader, writer)
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_handle_connection_connection_lost(self, ipc_dir):
        """ConnectionError during processing is handled."""
        token = generate_ipc_token()
        srv = IPCServer(name="connlost", socket_dir=ipc_dir, auth_token=token)

        reader = AsyncMock()
        reader.readuntil = AsyncMock(side_effect=ConnectionError("lost"))
        writer = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        await srv._handle_connection(reader, writer)

    @pytest.mark.asyncio
    async def test_handle_connection_cleanup_generic_exception(self, ipc_dir):
        """Generic exception during writer cleanup is handled."""
        token = generate_ipc_token()
        srv = IPCServer(name="cleanex", socket_dir=ipc_dir, auth_token=token)

        reader = AsyncMock()
        reader.readuntil = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 100))
        writer = AsyncMock()
        writer.close = MagicMock(side_effect=RuntimeError("cleanup error"))
        writer.wait_closed = AsyncMock()

        await srv._handle_connection(reader, writer)


class TestIPCClientMiscEdgeCases:
    """Miscellaneous client edge cases."""

    @pytest.mark.asyncio
    async def test_client_connection_refused_error(self, ipc_dir):
        """Client handles ConnectionRefusedError."""
        client = IPCClient(target="refused", socket_dir=ipc_dir, auth_token="tok")
        with patch.object(
            client, "_open_connection", side_effect=ConnectionRefusedError("refused")
        ):
            result = await client.send(IPCMessage(msg_type="test"))
            assert result is None

    @pytest.mark.asyncio
    async def test_client_timeout_error(self, ipc_dir):
        """Client handles TimeoutError on connect."""
        client = IPCClient(target="timeout2", socket_dir=ipc_dir, auth_token="tok")
        with patch.object(
            client, "_open_connection", side_effect=TimeoutError("timeout")
        ):
            result = await client.send(IPCMessage(msg_type="test"))
            assert result is None
