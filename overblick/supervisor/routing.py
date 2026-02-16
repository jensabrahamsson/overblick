"""
Inter-Agent Message Routing — star topology via Supervisor.

All agent-to-agent communication flows through the Supervisor:
    Agent A → Supervisor IPC → Agent B

This provides:
- Centralized audit trail for all inter-agent messages
- Permission enforcement (boss can block/approve routing)
- Decoupling — agents don't need to know each other's addresses
- Dead letter queue for undeliverable messages

Message types:
- route_message: Forward a message to another agent
- route_response: Response from the target agent
- broadcast: Send to all agents matching a filter

Usage by plugins:
    # From Telegram plugin, forward to Gmail
    await ctx.event_bus.emit("route_request",
        target="cherry",
        message_type="email_compose",
        payload={"to": "user@example.com", "body": "..."},
    )
"""

import logging
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RouteStatus(Enum):
    """Status of a routed message."""
    PENDING = "pending"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    DEAD_LETTER = "dead_letter"
    EXPIRED = "expired"


class RoutedMessage(BaseModel):
    """A message being routed between agents."""
    message_id: str
    source_agent: str
    target_agent: str
    message_type: str
    payload: dict[str, Any] = {}
    status: RouteStatus = RouteStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    delivered_at: Optional[float] = None
    response: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    ttl_seconds: float = 300.0  # 5 minute default TTL

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "message_type": self.message_type,
            "payload": self.payload,
            "status": self.status.value,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "error": self.error,
        }


class AgentCapabilities(BaseModel):
    """Declares what message types an agent accepts."""
    identity: str
    accepted_types: set[str] = set()
    max_queue_size: int = 100

    def accepts(self, message_type: str) -> bool:
        """Check if this agent accepts a message type."""
        if not self.accepted_types:
            return True  # Accept all if no filter
        return message_type in self.accepted_types


class MessageRouter:
    """
    Routes messages between agents via the Supervisor.

    All routing goes through a central queue. Messages are delivered
    when the target agent polls or when pushed via IPC.
    """

    MAX_DELIVERED = 1000
    MAX_DEAD_LETTERS = 1000

    def __init__(self, audit_log: Any = None):
        self._capabilities: dict[str, AgentCapabilities] = {}
        self._pending: list[RoutedMessage] = []
        self._dead_letters: list[RoutedMessage] = []
        self._delivered: list[RoutedMessage] = []
        self._audit = audit_log
        self._message_counter = 0

    def register_agent(
        self,
        identity: str,
        accepted_types: Optional[set[str]] = None,
        max_queue_size: int = 100,
    ) -> None:
        """Register an agent's message capabilities."""
        self._capabilities[identity] = AgentCapabilities(
            identity=identity,
            accepted_types=accepted_types or set(),
            max_queue_size=max_queue_size,
        )
        logger.info(
            "Registered agent '%s' for routing (accepts: %s)",
            identity,
            accepted_types or "all",
        )

    def unregister_agent(self, identity: str) -> None:
        """Remove an agent from routing."""
        self._capabilities.pop(identity, None)
        logger.info("Unregistered agent '%s' from routing", identity)

    def route(
        self,
        source: str,
        target: str,
        message_type: str,
        payload: Optional[dict[str, Any]] = None,
        ttl_seconds: float = 300.0,
    ) -> RoutedMessage:
        """
        Route a message from one agent to another.

        Args:
            source: Source agent identity
            target: Target agent identity
            message_type: Type of message (e.g. "email_compose", "status_query")
            payload: Message data
            ttl_seconds: Time-to-live before message expires

        Returns:
            RoutedMessage with initial PENDING status
        """
        self._message_counter += 1
        msg = RoutedMessage(
            message_id=f"route-{self._message_counter:06d}",
            source_agent=source,
            target_agent=target,
            message_type=message_type,
            payload=payload or {},
            ttl_seconds=ttl_seconds,
        )

        # Check if target agent exists and accepts this message type
        caps = self._capabilities.get(target)
        if caps is None:
            msg.status = RouteStatus.DEAD_LETTER
            msg.error = f"Unknown target agent: {target}"
            self._dead_letters.append(msg)
            self._log_route(msg, success=False)
            return msg

        if not caps.accepts(message_type):
            msg.status = RouteStatus.REJECTED
            msg.error = f"Agent '{target}' does not accept '{message_type}'"
            self._dead_letters.append(msg)
            self._log_route(msg, success=False)
            return msg

        # Check queue size
        target_pending = sum(
            1 for m in self._pending if m.target_agent == target
        )
        if target_pending >= caps.max_queue_size:
            msg.status = RouteStatus.REJECTED
            msg.error = f"Agent '{target}' queue full ({caps.max_queue_size})"
            self._dead_letters.append(msg)
            self._log_route(msg, success=False)
            return msg

        self._pending.append(msg)
        self._cleanup_if_needed()
        self._log_route(msg, success=True)
        return msg

    def broadcast(
        self,
        source: str,
        message_type: str,
        payload: Optional[dict[str, Any]] = None,
        exclude: Optional[set[str]] = None,
    ) -> list[RoutedMessage]:
        """
        Broadcast a message to all agents that accept the message type.

        Args:
            source: Source agent identity
            message_type: Type of message
            payload: Message data
            exclude: Agent identities to exclude (always excludes source)

        Returns:
            List of RoutedMessage objects (one per recipient)
        """
        exclude = exclude or set()
        exclude.add(source)

        messages = []
        for identity, caps in self._capabilities.items():
            if identity in exclude:
                continue
            if caps.accepts(message_type):
                msg = self.route(source, identity, message_type, payload)
                messages.append(msg)

        return messages

    def collect(self, agent: str) -> list[RoutedMessage]:
        """
        Collect pending messages for an agent.

        Returns and removes all pending messages for the agent.
        Expired messages are moved to dead letters.
        """
        collected = []
        remaining = []

        for msg in self._pending:
            if msg.target_agent == agent:
                if msg.is_expired:
                    msg.status = RouteStatus.EXPIRED
                    self._dead_letters.append(msg)
                else:
                    msg.status = RouteStatus.DELIVERED
                    msg.delivered_at = time.time()
                    collected.append(msg)
                    self._delivered.append(msg)
            else:
                remaining.append(msg)

        self._pending = remaining
        return collected

    def get_pending_count(self, agent: Optional[str] = None) -> int:
        """Get count of pending messages, optionally filtered by agent."""
        if agent:
            return sum(1 for m in self._pending if m.target_agent == agent)
        return len(self._pending)

    def get_dead_letters(self, limit: int = 50) -> list[RoutedMessage]:
        """Get recent dead letter messages."""
        return self._dead_letters[-limit:]

    def get_stats(self) -> dict:
        """Get routing statistics."""
        return {
            "total_routed": self._message_counter,
            "pending": len(self._pending),
            "delivered": len(self._delivered),
            "dead_letters": len(self._dead_letters),
            "registered_agents": len(self._capabilities),
            "agents": {
                name: {
                    "accepted_types": sorted(caps.accepted_types) if caps.accepted_types else ["*"],
                    "pending": sum(1 for m in self._pending if m.target_agent == name),
                }
                for name, caps in self._capabilities.items()
            },
        }

    def cleanup_expired(self) -> int:
        """Move expired pending messages to dead letters. Returns count."""
        expired = [m for m in self._pending if m.is_expired]
        for msg in expired:
            msg.status = RouteStatus.EXPIRED
            self._dead_letters.append(msg)
        self._pending = [m for m in self._pending if not m.is_expired]
        self._cap_lists()
        return len(expired)

    def _cleanup_if_needed(self) -> None:
        """Periodic cleanup: expire pending + cap lists."""
        # Run expiry check every 100 messages
        if self._message_counter % 100 == 0:
            self.cleanup_expired()
        self._cap_lists()

    def _cap_lists(self) -> None:
        """FIFO eviction for delivered and dead_letters lists."""
        if len(self._delivered) > self.MAX_DELIVERED:
            self._delivered = self._delivered[-self.MAX_DELIVERED:]
        if len(self._dead_letters) > self.MAX_DEAD_LETTERS:
            self._dead_letters = self._dead_letters[-self.MAX_DEAD_LETTERS:]

    def _log_route(self, msg: RoutedMessage, success: bool) -> None:
        """Log routing action to audit."""
        if not self._audit:
            return
        self._audit.log(
            action="message_route",
            category="routing",
            details=msg.to_dict(),
            success=success,
        )
