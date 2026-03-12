from __future__ import annotations

"""
Supervisor service — read-only agent status via IPC.

Connects to the Supervisor via Unix socket (macOS/Linux) or TCP
localhost (Windows) to request agent status.
Falls back gracefully if supervisor is not running.
"""

import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SupervisorService:
    """Read-only access to supervisor agent status via IPC."""

    # Cache status for 5 seconds to reduce IPC load
    _CACHE_TTL = 5.0

    def __init__(self, socket_dir: Path | None = None):
        self._socket_dir = socket_dir
        self._resolved_socket_dir: Path | None = None
        self._status_cache: dict[str, Any] | None = None
        self._status_cache_time: float = 0.0

    def _invalidate_cache(self) -> None:
        """Invalidate status cache (call after agent start/stop)."""
        self._status_cache = None

    def _resolve_socket_dir(self) -> Path:
        """Resolve the IPC socket directory."""
        if self._resolved_socket_dir:
            return self._resolved_socket_dir
        if self._socket_dir:
            self._resolved_socket_dir = self._socket_dir
        else:
            base_dir = Path(__file__).parent.parent.parent.parent
            self._resolved_socket_dir = base_dir / "data" / "ipc"
        return self._resolved_socket_dir

    def _read_auth_token(self, socket_dir: Path) -> str:
        """Read and decrypt auth token from supervisor's token or conn file."""
        try:
            from overblick.supervisor.ipc import read_ipc_token

            return read_ipc_token("supervisor", socket_dir=socket_dir)
        except Exception as e:
            logger.debug("Failed to read auth token: %s", e)
        return ""

    def _get_client(self):
        """Create a fresh IPC client with current auth token.

        Re-reads the token/conn file each time to handle supervisor
        restarts that generate new tokens.
        """
        try:
            from overblick.shared.platform import IS_WINDOWS
            from overblick.supervisor.ipc import IPCClient, _read_conn_file

            socket_dir = self._resolve_socket_dir()
            auth_token = self._read_auth_token(socket_dir)

            # On Windows (or if .conn file exists), pass TCP port
            tcp_port = None
            conn_path = socket_dir / "overblick-supervisor.conn"
            if IS_WINDOWS or conn_path.exists():
                conn_info = _read_conn_file(conn_path)
                if conn_info:
                    tcp_port = conn_info.get("port")
                    if not auth_token and "token" in conn_info:
                        auth_token = conn_info["token"]

            return IPCClient(
                target="supervisor",
                socket_dir=socket_dir,
                auth_token=auth_token,
                tcp_port=tcp_port,
            )
        except Exception as e:
            logger.debug("IPC client not available: %s", e)
            return None

    async def get_status(self) -> dict[str, Any] | None:
        """
        Request status from supervisor.

        Returns:
            Status dict with agent info, or None if supervisor unavailable.
        """
        # Check cache first
        now = time.monotonic()
        if self._status_cache is not None and (now - self._status_cache_time) < self._CACHE_TTL:
            return self._status_cache

        client = self._get_client()
        if not client:
            return None

        try:
            status = await client.request_status(sender="dashboard")
            # Cache successful result
            if status is not None:
                self._status_cache = status
                self._status_cache_time = now
            return status
        except Exception as e:
            logger.debug("Supervisor not reachable: %s", e)
            return None

    async def is_running(self) -> bool:
        """Check if supervisor is reachable."""
        status = await self.get_status()
        return status is not None

    async def get_agents(self) -> list[dict[str, Any]]:
        """
        Get list of managed agents.

        Returns:
            List of agent status dicts, or empty list if unavailable.
        """
        status = await self.get_status()
        if not status:
            return []

        # Supervisor returns agents as dict {name: agent_dict}
        # Convert to list of dicts with 'name' field
        agents_dict = status.get("agents", {})
        if isinstance(agents_dict, dict):
            return [{**agent_data, "name": name} for name, agent_data in agents_dict.items()]
        return []

    async def start_agent(self, identity: str) -> dict[str, Any]:
        """Request supervisor to start an agent.

        Returns:
            Dict with 'success' bool and optional 'error' string.
        """
        client = self._get_client()
        if not client:
            return {"success": False, "error": "Supervisor not reachable"}

        try:
            from overblick.supervisor.ipc import IPCMessage

            msg = IPCMessage(
                msg_type="start_agent",
                payload={"identity": identity},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=10.0)
            # Invalidate cache since agent state may have changed
            self._invalidate_cache()
            if response and response.msg_type == "agent_action_response":
                return response.payload
            return {"success": False, "error": "No response from supervisor"}
        except Exception as e:
            logger.debug("Failed to start agent '%s': %s", identity, e)
            # Still invalidate cache in case partial change occurred
            self._invalidate_cache()
            return {"success": False, "error": str(e)}

    async def stop_agent(self, identity: str) -> dict[str, Any]:
        """Request supervisor to stop an agent.

        Returns:
            Dict with 'success' bool and optional 'error' string.
        """
        client = self._get_client()
        if not client:
            return {"success": False, "error": "Supervisor not reachable"}

        try:
            from overblick.supervisor.ipc import IPCMessage

            msg = IPCMessage(
                msg_type="stop_agent",
                payload={"identity": identity},
                sender="dashboard",
            )
            response = await client.send(msg, timeout=10.0)
            # Invalidate cache since agent state may have changed
            self._invalidate_cache()
            if response and response.msg_type == "agent_action_response":
                return response.payload
            return {"success": False, "error": "No response from supervisor"}
        except Exception as e:
            logger.debug("Failed to stop agent '%s': %s", identity, e)
            # Still invalidate cache in case partial change occurred
            self._invalidate_cache()
            return {"success": False, "error": str(e)}

    async def close(self) -> None:
        """Cleanup (no persistent state to release)."""
        pass
