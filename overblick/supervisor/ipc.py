"""
IPC — Inter-Process Communication for agent supervisor.

Uses Unix domain sockets for communication between the Supervisor
and managed agent processes. JSON-based message protocol.

SECURITY: Messages include an auth_token field. The server validates
the token before processing any message. Tokens are generated at
supervisor startup and shared with child processes via a token file
(mode 0o600) in the socket directory — never via environment variables.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default socket directory
_SOCKET_DIR = Path(tempfile.gettempdir()) / "overblick"

# Maximum IPC message size (1 MB) — prevents OOM via oversized messages
_MAX_MESSAGE_SIZE = 1024 * 1024


def generate_ipc_token() -> str:
    """Generate a cryptographically secure IPC authentication token."""
    return secrets.token_hex(32)


class IPCMessage(BaseModel):
    """A message in the IPC protocol."""
    msg_type: str
    payload: dict[str, Any] = {}
    sender: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    request_id: str = ""
    auth_token: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "type": self.msg_type,
            "payload": self.payload,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "auth_token": self.auth_token,
        })

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
        cls, resource: str, action: str, reason: str,
        sender: str = "", auth_token: str = "",
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
MessageHandler = Callable[[IPCMessage], Coroutine[Any, Any, Optional[IPCMessage]]]


class IPCServer:
    """
    Unix domain socket server for the Supervisor.

    Listens for connections from agent processes and dispatches
    messages to registered handlers.

    SECURITY: If an auth_token is set, all incoming messages must
    include a matching token or they are rejected.
    """

    def __init__(
        self,
        name: str = "supervisor",
        socket_dir: Optional[Path] = None,
        auth_token: str = "",
    ):
        self._name = name
        self._socket_dir = socket_dir or _SOCKET_DIR
        self._socket_path = self._socket_dir / f"overblick-{name}.sock"
        self._server: Optional[asyncio.AbstractServer] = None
        self._handlers: dict[str, MessageHandler] = {}
        self._auth_token = auth_token
        self._rejected_count = 0

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

    async def start(self) -> None:
        """Start listening for connections."""
        self._socket_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Write auth token to file for child processes (secure: mode 0o600)
        if self._auth_token:
            self.token_path.write_text(self._auth_token)
            os.chmod(str(self.token_path), 0o600)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self._socket_path),
        )

        # Restrict socket permissions (owner only)
        os.chmod(str(self._socket_path), 0o600)

        logger.info("IPC server listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Stop the server and cleanup socket + token file."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        if self._socket_path.exists():
            self._socket_path.unlink()

        # Clean up token file
        if self.token_path.exists():
            self.token_path.unlink()

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
            data = await reader.readline()
            if not data:
                return

            if len(data) > _MAX_MESSAGE_SIZE:
                logger.warning("IPC message too large (%d bytes), rejecting", len(data))
                return

            msg = IPCMessage.from_json(data.decode().strip())

            # Validate authentication
            if not self._validate_auth(msg):
                self._rejected_count += 1
                logger.warning(
                    "IPC auth rejected from sender '%s' (type: %s) — "
                    "total rejections: %d",
                    msg.sender, msg.msg_type, self._rejected_count,
                )
                return

            logger.debug("IPC received: %s from %s", msg.msg_type, msg.sender)

            handler = self._handlers.get(msg.msg_type)
            if handler:
                response = await handler(msg)
                if response:
                    writer.write((response.to_json() + "\n").encode())
                    await writer.drain()
            else:
                logger.warning("No handler for message type: %s", msg.msg_type)

        except json.JSONDecodeError as e:
            logger.warning("Invalid IPC message: %s", e)
        except Exception as e:
            logger.error("IPC handler error: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()


class IPCClient:
    """
    Unix domain socket client for agent processes.

    Connects to the Supervisor's socket to send messages
    and receive responses.

    If auth_token is set, it is included in all outgoing messages.
    """

    def __init__(
        self,
        target: str = "supervisor",
        socket_dir: Optional[Path] = None,
        auth_token: str = "",
    ):
        self._socket_dir = socket_dir or _SOCKET_DIR
        self._socket_path = self._socket_dir / f"overblick-{target}.sock"
        self._auth_token = auth_token

    async def send(self, message: IPCMessage, timeout: float = 5.0) -> Optional[IPCMessage]:
        """
        Send a message and optionally wait for a response.

        If auth_token is configured, it is injected into the message.
        Returns the response message, or None if no response.
        """
        # Inject auth token
        if self._auth_token and not message.auth_token:
            message.auth_token = self._auth_token

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=timeout,
            )

            try:
                writer.write((message.to_json() + "\n").encode())
                await writer.drain()

                # Wait for response
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=timeout)
                    if data:
                        return IPCMessage.from_json(data.decode().strip())
                except asyncio.TimeoutError:
                    logger.warning("IPC timeout waiting for response to %s", message.msg_type)
            finally:
                writer.close()
                await writer.wait_closed()

        except FileNotFoundError:
            logger.debug("IPC socket not found: %s", self._socket_path)
        except ConnectionRefusedError:
            logger.debug("IPC connection refused: %s", self._socket_path)
        except asyncio.TimeoutError:
            logger.warning("IPC timeout connecting to %s", self._socket_path)
        except Exception as e:
            logger.error("IPC client error: %s", e)

        return None

    async def request_status(self, sender: str = "") -> Optional[dict]:
        """Request status from the supervisor."""
        response = await self.send(
            IPCMessage.status_request(sender=sender, auth_token=self._auth_token)
        )
        if response and response.msg_type == "status_response":
            return response.payload
        return None

    async def request_permission(
        self, resource: str, action: str, reason: str, sender: str = "",
    ) -> bool:
        """Request permission from the supervisor."""
        msg = IPCMessage.permission_request(
            resource, action, reason, sender=sender, auth_token=self._auth_token,
        )
        response = await self.send(msg)
        if response and response.msg_type == "permission_response":
            return response.payload.get("granted", False)
        return False
