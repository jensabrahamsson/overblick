"""
Supervisor service — read-only agent status via IPC.

Connects to the Supervisor's Unix socket to request agent status.
Falls back gracefully if supervisor is not running.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SupervisorService:
    """Read-only access to supervisor agent status via IPC."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-initialize IPC client."""
        if self._client is not None:
            return self._client
        try:
            from overblick.supervisor.ipc import IPCClient
            self._client = IPCClient(target="supervisor")
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
        return status.get("agents", [])

    async def close(self) -> None:
        """Cleanup IPC client."""
        self._client = None
