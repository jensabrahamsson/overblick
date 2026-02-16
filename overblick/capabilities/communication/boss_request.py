"""
Boss request capability — ask the supervisor for research or guidance.

Enables agents to request web research from the supervisor via IPC.
The supervisor performs the actual search (agents don't have internet
access) and returns a summarized result.

Security:
- All requests go through authenticated IPC (Unix sockets)
- Audit logging of all requests and responses
- Timeout protection (60s default for research requests)
"""

import logging
from typing import Optional

from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)

# Research requests involve web search + LLM summarization
_RESEARCH_TIMEOUT = 60.0


class BossRequestCapability:
    """
    Ask the supervisor for research via IPC.

    Requires an IPC client in the plugin context. Degrades gracefully
    (configured=False) when IPC is not available.

    Usage:
        boss = ctx.get_capability("boss_request")
        if boss and boss.configured:
            result = await boss.request_research("What is the current EUR/SEK rate?")
    """

    name = "boss_request"

    def __init__(self, ctx):
        self.ctx = ctx
        self._ipc_client = None

    async def setup(self) -> None:
        """Get IPC client from context."""
        self._ipc_client = self.ctx.ipc_client
        if self._ipc_client:
            logger.info(
                "BossRequestCapability ready for identity %s",
                self.ctx.identity_name,
            )
        else:
            logger.warning(
                "BossRequestCapability: no IPC client for identity %s "
                "— research requests disabled",
                self.ctx.identity_name,
            )

    @property
    def configured(self) -> bool:
        """Whether the capability has an IPC client available."""
        return self._ipc_client is not None

    async def request_research(
        self, query: str, context: str = "",
    ) -> Optional[str]:
        """
        Ask the supervisor to research a query via web search.

        Args:
            query: The research question (should be in English).
            context: Optional context about why the research is needed.

        Returns:
            Research summary as string, or None on failure/timeout.
        """
        if not self._ipc_client:
            logger.warning("BossRequestCapability: not configured, cannot send request")
            return None

        msg = IPCMessage(
            msg_type="research_request",
            payload={
                "query": query,
                "context": context,
            },
            sender=self.ctx.identity_name,
        )

        logger.info(
            "Research request from '%s': %s",
            self.ctx.identity_name, query[:100],
        )

        try:
            response = await self._ipc_client.send(msg, timeout=_RESEARCH_TIMEOUT)
            if response and response.payload:
                summary = response.payload.get("summary", "")
                if summary:
                    logger.info(
                        "Research response received (%d chars)",
                        len(summary),
                    )
                    return summary
                error = response.payload.get("error", "")
                if error:
                    logger.warning("Research request failed: %s", error)
                    return None
        except Exception as e:
            logger.error("Research request IPC failed: %s", e, exc_info=True)

        return None

    async def teardown(self) -> None:
        """Cleanup (no-op — IPC client lifecycle is managed by orchestrator)."""
        pass
