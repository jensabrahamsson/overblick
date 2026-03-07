"""
IPC — Inter-Process Communication for agent supervisor.

Uses Unix domain sockets (macOS/Linux) or TCP localhost (Windows)
for communication between the Supervisor and managed agent processes.
JSON-based message protocol.

SECURITY: Messages include an auth_token field. The server validates
the token before processing any message. Tokens are generated at
supervisor startup and shared with child processes via a token file
(mode 0o600 on Unix) in the socket directory — never via environment variables.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import tempfile
import time
from collections import deque
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from overblick.shared.platform import (
    IS_WINDOWS,
    set_restrictive_dir_permissions,
    set_restrictive_permissions,
)

logger = logging.getLogger(__name__)

# Default socket directory
_SOCKET_DIR = Path(tempfile.gettempdir()) / "overblick"

# Maximum IPC message size (1 MB) — prevents OOM via oversized messages
_MAX_MESSAGE_SIZE = 1024 * 1024


def generate_ipc_token() -> str:
    """Generate a cryptographically secure IPC authentication token."""
    return secrets.token_hex(32)


def _obfuscate_token(token: str) -> bytes:
    """Obfuscate IPC token with Fernet (key + encrypted token stored together).

    Security relies on filesystem permissions (0o600) preventing unauthorized
    read access.
    """
    try:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        f = Fernet(key)
        encrypted = f.encrypt(token.encode())
        # Return key + newline + encrypted token
        return key + b"\n" + encrypted
    except ImportError:
        from overblick.core.security.settings import safe_mode

        if safe_mode():
            raise RuntimeError(
                "cryptography library missing. IPC token cannot be obfuscated "
                "in safe mode. Install with 'pip install cryptography'."
            )
        logger.warning("cryptography not installed — IPC token stored as plaintext")
        return token.encode()


def _deobfuscate_token(data: bytes) -> str:
    """Deobfuscate IPC token written by _obfuscate_token."""
    try:
        from cryptography.fernet import Fernet

        parts = data.split(b"\n", 1)
        if len(parts) == 2:
            key, encrypted = parts
            f = Fernet(key)
            return f.decrypt(encrypted).decode()
    except ImportError:
        from overblick.core.security.settings import safe_mode

        if safe_mode():
            raise RuntimeError(
                "cryptography library missing. IPC token cannot be deobfuscated "
                "in safe mode. Install with 'pip install cryptography'."
            )
    except Exception as e:
        logger.warning("Failed to deobfuscate IPC token: %s", e)
    # Fallback: plaintext (backward compat or no cryptography)
    return data.decode().strip()


# ─────────────────────────────────────────────────────────────────────────────
# IPC CONNECTION RATE LIMITER
# ─────────────────────────────────────────────────────────────────────────────


class _IPCRateLimiter:
    """Per-sender rate limiter for IPC connections.

    Uses a sliding window log with O(1) operations via deque.
    Each sender is limited to max_per_minute connections.
    """

    def __init__(self, max_per_minute: int = 100):
        self._max_per_minute = max_per_minute
        self._counters: dict[str, deque[float]] = {}

    # Maximum tracked senders (prevents unbounded memory growth)
    _MAX_TRACKED_SENDERS = 1000

    def allow(self, sender: str) -> bool:
        """Check if sender is within rate limit."""
        now = time.monotonic()
        cutoff = now - 60.0

        if sender not in self._counters:
            # Evict least-recently-active senders if over limit BEFORE adding new one
            if len(self._counters) >= self._MAX_TRACKED_SENDERS:
                # O(1) eviction of an arbitrary sender
                self._counters.pop(next(iter(self._counters)))
            self._counters[sender] = deque()

        counter = self._counters[sender]

        # Prune old entries from the left
        while counter and counter[0] <= cutoff:
            counter.popleft()

        # Rate check
        if len(counter) >= self._max_per_minute:
            return False

        # Record this request
        counter.append(now)
        return True


def _read_conn_file(conn_path: Path) -> dict | None:
    """Read and deobfuscate connection info from .conn file (Windows TCP mode).

    Returns dict with 'port' and 'token' keys, or None on failure.
    """
    if not conn_path.exists():
        return None
    data = conn_path.read_bytes()
    try:
        from cryptography.fernet import Fernet

        parts = data.split(b"\n", 1)
        if len(parts) == 2:
            key, encrypted = parts
            f = Fernet(key)
            decrypted = f.decrypt(encrypted).decode()
            return json.loads(decrypted)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to deobfuscate conn file %s: %s", conn_path, e)

    # Fallback: try plaintext JSON
    try:
        return json.loads(data.decode())
    except Exception:
        return None


def read_ipc_token(name: str = "supervisor", socket_dir: Path | None = None) -> str:
    """Read and deobfuscate IPC token from file (for child processes).

    On Unix: reads from .token file (Fernet-obfuscated token).
    On Windows: reads from .conn file (Fernet-obfuscated JSON with port + token).
    Falls back to .token if .conn is not found.
    """
    sd = socket_dir or _SOCKET_DIR

    # Try .conn file first (Windows TCP mode, or if it exists)
    conn_path = sd / f"overblick-{name}.conn"
    if conn_path.exists():
        conn_info = _read_conn_file(conn_path)
        if conn_info and "token" in conn_info:
            return conn_info["token"]

    # Standard .token file (Unix mode)
    token_path = sd / f"overblick-{name}.token"
    if not token_path.exists():
        return ""
    return _deobfuscate_token(token_path.read_bytes())


class IPCMessage(BaseModel):
    """A message in the IPC protocol."""

    msg_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sender: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    request_id: str = ""
    auth_token: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.msg_type,
                "payload": self.payload,
                "sender": self.sender,
                "timestamp": self.timestamp,
                "request_id": self.request_id,
                "auth_token": self.auth_token,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "IPCMessage":
        d = json.loads(data)
        return cls(
            msg_type=d["type"],
            payload=d.get("payload", {}),
            sender=d.get("sender", ""),
            timestamp=d.get("timestamp", ""),
            request_id=d.get("request_id", ""),
            auth_token=d.get("auth_token", ""),
        )

    @classmethod
    def status_request(cls, sender: str = "", auth_token: str = "") -> "IPCMessage":
        return cls(msg_type="status_request", sender=sender, auth_token=auth_token)

    @classmethod
    def status_response(cls, status: dict, sender: str = "") -> "IPCMessage":
        return cls(msg_type="status_response", payload=status, sender=sender)

    @classmethod
    def permission_request(
        cls,
        resource: str,
        action: str,
        reason: str,
        sender: str = "",
        auth_token: str = "",
    ) -> "IPCMessage":
        return cls(
            msg_type="permission_request",
            payload={"resource": resource, "action": action, "reason": reason},
            sender=sender,
            auth_token=auth_token,
        )

    @classmethod
    def permission_response(cls, granted: bool, reason: str = "", sender: str = "") -> "IPCMessage":
        return cls(
            msg_type="permission_response",
            payload={"granted": granted, "reason": reason},
            sender=sender,
        )

    @classmethod
    def shutdown(cls, sender: str = "", auth_token: str = "") -> "IPCMessage":
        return cls(msg_type="shutdown", sender=sender, auth_token=auth_token)


# Type alias for message handlers
MessageHandler = Callable[[IPCMessage], Coroutine[Any, Any, IPCMessage | None]]


class IPCServer:
    """
    IPC server for the Supervisor.

    Uses Unix domain sockets on macOS/Linux, TCP localhost on Windows.
    Listens for connections from agent processes and dispatches
    messages to registered handlers.

    SECURITY: If an auth_token is set, all incoming messages must
    include a matching token or they are rejected.
    """

    def __init__(
        self,
        name: str = "supervisor",
        socket_dir: Path | None = None,
        auth_token: str = "",
        rate_limit_per_minute: int = 100,
    ):
        self._name = name
        self._socket_dir = socket_dir or _SOCKET_DIR
        self._socket_path = self._socket_dir / f"overblick-{name}.sock"
        self._conn_path = self._socket_dir / f"overblick-{name}.conn"
        self._server: asyncio.AbstractServer | None = None
        self._handlers: dict[str, MessageHandler] = {}
        self._auth_token = auth_token
        self._rejected_count = 0
        self._rate_limiter = _IPCRateLimiter(max_per_minute=rate_limit_per_minute)
        self._rate_limited_count = 0
        self._tcp_port: int | None = None

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def on(self, msg_type: str, handler: MessageHandler) -> None:
        """Register a handler for a message type."""
        self._handlers[msg_type] = handler

    @property
    def token_path(self) -> Path:
        """Path to the auth token file (for sharing with child processes)."""
        return self._socket_dir / f"overblick-{self._name}.token"

    @property
    def tcp_port(self) -> int | None:
        """TCP port used on Windows (None on Unix)."""
        return self._tcp_port

    async def start(self) -> None:
        """Start listening for connections.

        Unix: creates a Unix domain socket at socket_path.
        Windows: binds TCP on 127.0.0.1:0 (OS-assigned port), writes
        connection info to .conn file.

        Cleans up stale connection/socket files before starting.
        """
        self._socket_dir.mkdir(parents=True, exist_ok=True)
        set_restrictive_dir_permissions(self._socket_dir)

        # Cleanup stale .conn file from previous runs
        if self._conn_path.exists():
            logger.debug("Removing stale conn file: %s", self._conn_path)
            self._conn_path.unlink(missing_ok=True)

        if IS_WINDOWS:
            await self._start_tcp()
        else:
            await self._start_unix()

    async def _start_unix(self) -> None:
        """Start Unix domain socket server."""
        # Remove stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Write auth token to encrypted file for child processes
        self._write_token_file()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self._socket_path),
            limit=_MAX_MESSAGE_SIZE,
        )

        # Restrict socket permissions (owner only)
        set_restrictive_permissions(self._socket_path)

        logger.info("IPC server listening on %s", self._socket_path)

    async def _start_tcp(self) -> None:
        """Start TCP localhost server (Windows fallback).

        Uses SO_EXCLUSIVEADDRUSE on Windows to prevent port hijacking.
        """

        def _apply_socket_options(sock: socket.socket) -> None:
            """Apply security options to the TCP server socket."""
            # SO_EXCLUSIVEADDRUSE only exists on actual Windows (not mocked)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Prevents other processes from binding to the same port
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            elif IS_WINDOWS:
                # Fallback: try the known Windows constant value
                try:
                    SO_EXCLUSIVEADDRUSE = ~socket.SO_REUSEADDR
                    sock.setsockopt(socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    logger.debug("SO_EXCLUSIVEADDRUSE not supported")

        # Bind to OS-assigned port on loopback only
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _apply_socket_options(server_sock)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.setblocking(False)
        self._tcp_port = server_sock.getsockname()[1]

        self._server = await asyncio.start_server(
            self._handle_connection,
            sock=server_sock,
            limit=_MAX_MESSAGE_SIZE,
        )

        # Write connection info: Fernet key + obfuscated JSON with port and token
        self._write_conn_file()

        logger.info("IPC server listening on 127.0.0.1:%d", self._tcp_port)

    def _write_token_file(self) -> None:
        """Write obfuscated auth token to file (Unix)."""
        if not self._auth_token:
            return
        encrypted_data = _obfuscate_token(self._auth_token)
        if IS_WINDOWS:
            self.token_path.write_bytes(encrypted_data)
        else:
            fd = os.open(
                str(self.token_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(encrypted_data)

    def _write_conn_file(self) -> None:
        """Write connection info file (Windows TCP mode).

        Format: <fernet_key>\\n<encrypted_json>
        where JSON = {"port": N, "token": "<hex>"}

        The port is always written (required for client connection).
        The token may be empty if auth is disabled.

        Uses atomic write (write to temp, then rename) to prevent
        readers from seeing partial data.
        """
        if self._tcp_port is None:
            return

        conn_data = json.dumps(
            {
                "port": self._tcp_port,
                "token": self._auth_token,
            }
        )

        # Write atomically: temp file → rename
        tmp_path = self._conn_path.with_suffix(".tmp")
        try:
            from cryptography.fernet import Fernet

            key = Fernet.generate_key()
            f = Fernet(key)
            encrypted = f.encrypt(conn_data.encode())
            tmp_path.write_bytes(key + b"\n" + encrypted)
        except ImportError:
            # Fallback: plaintext JSON (not recommended for production)
            tmp_path.write_text(conn_data)

        tmp_path.replace(self._conn_path)

    async def stop(self) -> None:
        """Stop the server and cleanup socket/token/conn files."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Cleanup Unix socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Cleanup token file (Unix)
        if self.token_path.exists():
            self.token_path.unlink()

        # Cleanup conn file (Windows)
        if self._conn_path.exists():
            self._conn_path.unlink()

        self._tcp_port = None
        logger.info("IPC server stopped")

    def _validate_auth(self, msg: IPCMessage) -> bool:
        """Validate message authentication token."""
        if not self._auth_token:
            return True  # Auth disabled
        return hmac.compare_digest(msg.auth_token, self._auth_token)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        try:
            data = await reader.readuntil(b"\n")
            if not data:
                return

            msg = IPCMessage.from_json(data.decode().strip())

            # Rate limit per sender
            sender_key = msg.sender or "unknown"
            if not self._rate_limiter.allow(sender_key):
                self._rate_limited_count += 1
                logger.warning(
                    "IPC rate limited sender '%s' (total: %d)",
                    sender_key,
                    self._rate_limited_count,
                )
                return

            # Validate authentication
            if not self._validate_auth(msg):
                self._rejected_count += 1
                logger.warning(
                    "IPC auth rejected from sender '%s' (type: %s) — " "total rejections: %d",
                    msg.sender,
                    msg.msg_type,
                    self._rejected_count,
                )
                return

            logger.debug("IPC received: %s from %s", msg.msg_type, msg.sender)

            handler = self._handlers.get(msg.msg_type)
            if handler:
                response = await handler(msg)
                if response:
                    try:
                        writer.write((response.to_json() + "\n").encode())
                        await writer.drain()
                    except (ConnectionError, asyncio.TimeoutError):
                        logger.debug("IPC client disconnected before response could be sent")
            else:
                logger.warning("No handler for message type: %s", msg.msg_type)

        except asyncio.LimitOverrunError:
            logger.warning("IPC message exceeds buffer limit, rejecting")
        except asyncio.IncompleteReadError:
            logger.debug("IPC client disconnected before sending complete message")
        except (ConnectionError, asyncio.TimeoutError):
            logger.debug("IPC connection lost during processing")
        except json.JSONDecodeError as e:
            logger.warning("Invalid IPC message: %s", e)
        except Exception as e:
            logger.error("IPC handler error: %s", e, exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, asyncio.TimeoutError, OSError):
                # Ignore errors during final cleanup of a broken pipe
                pass
            except Exception as e:
                logger.debug("Error during IPC cleanup: %s", e)


class IPCClient:
    """
    IPC client for agent processes.

    Connects to the Supervisor via Unix domain socket (macOS/Linux)
    or TCP localhost (Windows) to send messages and receive responses.

    If auth_token is set, it is included in all outgoing messages.
    """

    def __init__(
        self,
        target: str = "supervisor",
        socket_dir: Path | None = None,
        auth_token: str = "",
        tcp_port: int | None = None,
    ):
        self._socket_dir = socket_dir or _SOCKET_DIR
        self._target = target
        self._socket_path = self._socket_dir / f"overblick-{target}.sock"
        self._conn_path = self._socket_dir / f"overblick-{target}.conn"
        self._auth_token = auth_token
        self._tcp_port = tcp_port

    async def _open_connection(self, timeout: float) -> tuple:
        """Open a connection using the appropriate transport.

        Returns (reader, writer) pair.
        """
        if IS_WINDOWS or self._tcp_port is not None:
            port = self._tcp_port or self._read_tcp_port()
            if port is None:
                raise FileNotFoundError(f"No connection file found: {self._conn_path}")
            return await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=timeout,
            )
        else:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=timeout,
            )

    def _read_tcp_port(self) -> int | None:
        """Read TCP port from .conn file (Windows mode)."""
        if not self._conn_path.exists():
            return None
        try:
            conn_info = _read_conn_file(self._conn_path)
            if conn_info:
                # Also update auth token if not set
                if not self._auth_token and "token" in conn_info:
                    self._auth_token = conn_info["token"]
                return conn_info.get("port")
        except Exception as e:
            logger.debug("Failed to read conn file: %s", e)
        return None

    async def send(self, message: IPCMessage, timeout: float = 5.0) -> IPCMessage | None:
        """
        Send a message and optionally wait for a response.

        If auth_token is configured, it is injected into the message.
        Returns the response message, or None if no response.
        """
        # Inject auth token (copy to avoid mutating caller's message object)
        if self._auth_token and not message.auth_token:
            message = message.model_copy(update={"auth_token": self._auth_token})

        try:
            reader, writer = await self._open_connection(timeout)

            try:
                writer.write((message.to_json() + "\n").encode())
                await writer.drain()

                # Wait for response
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=timeout)
                    if data:
                        return IPCMessage.from_json(data.decode().strip())
                except TimeoutError:
                    logger.warning("IPC timeout waiting for response to %s", message.msg_type)
            finally:
                writer.close()
                await writer.wait_closed()

        except FileNotFoundError:
            logger.debug("IPC socket/conn not found: %s", self._socket_path)
        except ConnectionRefusedError:
            logger.debug("IPC connection refused: %s", self._socket_path)
        except TimeoutError:
            logger.warning("IPC timeout connecting to %s", self._socket_path)
        except Exception as e:
            logger.error("IPC client error: %s", e, exc_info=True)

        return None

    async def request_status(self, sender: str = "") -> dict | None:
        """Request status from the supervisor."""
        response = await self.send(
            IPCMessage.status_request(sender=sender, auth_token=self._auth_token)
        )
        if response and response.msg_type == "status_response":
            return response.payload
        return None

    async def request_permission(
        self,
        resource: str,
        action: str,
        reason: str,
        sender: str = "",
    ) -> bool:
        """Request permission from the supervisor."""
        msg = IPCMessage.permission_request(
            resource,
            action,
            reason,
            sender=sender,
            auth_token=self._auth_token,
        )
        response = await self.send(msg)
        if response and response.msg_type == "permission_response":
            return response.payload.get("granted", False)
        return False
