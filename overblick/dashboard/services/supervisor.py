"""
Supervisor service — read-only agent status via IPC.

Connects to the Supervisor's Unix socket to request agent status.
Falls back gracefully if supervisor is not running.
"""

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SupervisorService:
    """Read-only access to supervisor agent status via IPC."""

    def __init__(self, socket_dir: Optional[Path] = None):
        self._client = None
        self._socket_dir = socket_dir

    def _read_auth_token(self, socket_dir: Path) -> str:
        """Read auth token from supervisor's token file."""
        token_path = socket_dir / "overblick-supervisor.token"
        try:
            if token_path.exists():
                return token_path.read_text().strip()
        except Exception as e:
            logger.debug("Failed to read auth token: %s", e)
        return ""

    def _get_client(self):
        """Lazy-initialize IPC client with auth token."""
        if self._client is not None:
            return self._client
        try:
            from overblick.supervisor.ipc import IPCClient

            # Determine socket directory
            socket_dir = self._socket_dir
            if not socket_dir:
                # Default to project data/ipc directory
                from pathlib import Path
                base_dir = Path(__file__).parent.parent.parent.parent
                socket_dir = base_dir / "data" / "ipc"

            # Read auth token
            auth_token = self._read_auth_token(socket_dir)

            self._client = IPCClient(
                target="supervisor",
                socket_dir=socket_dir,
                auth_token=auth_token,
            )
            return self._client
        except Exception as e:
            logger.debug("IPC client not available: %s", e)
            return None

    async def get_status(self) -> Optional[dict[str, Any]]:
        """
        Request status from supervisor.

        Returns:
            Status dict with agent info, or None if supervisor unavailable.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            return await client.request_status(sender="dashboard")
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
            return [
                {**agent_data, "name": name}
                for name, agent_data in agents_dict.items()
            ]
        return []

    async def close(self) -> None:
        """Cleanup IPC client."""
        self._client = None
