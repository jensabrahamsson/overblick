"""
Inter-agent message routing tests.

Tests cover:
- Agent registration and capability declaration
- Message routing between agents
- Broadcast messaging
- Dead letter queue for undeliverable messages
- Message expiration and TTL
- Queue overflow protection
- Collect and delivery tracking
- Routing statistics
"""

import time
from unittest.mock import MagicMock

import pytest

from overblick.supervisor.routing import (
    AgentCapabilities,
    MessageRouter,
    RouteStatus,
    RoutedMessage,
)


# ---------------------------------------------------------------------------
# AgentCapabilities tests
# ---------------------------------------------------------------------------

class TestAgentCapabilities:
    """Test agent capability declarations."""

    def test_accepts_all_when_empty(self):
        caps = AgentCapabilities(identity="volt")
        assert caps.accepts("anything")
        assert caps.accepts("email_compose")

    def test_accepts_only_listed_types(self):
        caps = AgentCapabilities(
            identity="birch",
            accepted_types={"question", "meditation"},
        )
        assert caps.accepts("question")
        assert caps.accepts("meditation")
        assert not caps.accepts("alert")

    def test_default_queue_size(self):
        caps = AgentCapabilities(identity="nyx")
        assert caps.max_queue_size == 100


# ---------------------------------------------------------------------------
# RoutedMessage tests
# ---------------------------------------------------------------------------

class TestRoutedMessage:
    """Test routed message dataclass."""

    def test_construction(self):
        msg = RoutedMessage(
            message_id="route-001",
            source_agent="volt",
            target_agent="birch",
            message_type="question",
        )
        assert msg.status == RouteStatus.PENDING
        assert msg.created_at > 0
        assert msg.delivered_at is None

    def test_is_expired(self):
        msg = RoutedMessage(
            message_id="route-001",
            source_agent="volt",
            target_agent="birch",
            message_type="question",
            ttl_seconds=0.0,
            created_at=time.time() - 1,
        )
        assert msg.is_expired

    def test_not_expired(self):
        msg = RoutedMessage(
            message_id="route-001",
            source_agent="volt",
            target_agent="birch",
            message_type="question",
            ttl_seconds=300.0,
        )
        assert not msg.is_expired

    def test_to_dict(self):
        msg = RoutedMessage(
            message_id="route-001",
            source_agent="volt",
            target_agent="nyx",
            message_type="paradox",
            payload={"text": "What is nothing?"},
        )
        d = msg.to_dict()
        assert d["message_id"] == "route-001"
        assert d["source_agent"] == "volt"
        assert d["target_agent"] == "nyx"
        assert d["payload"]["text"] == "What is nothing?"
        assert d["status"] == "pending"


# ---------------------------------------------------------------------------
# MessageRouter tests
# ---------------------------------------------------------------------------

class TestMessageRouter:
    """Test message routing functionality."""

    def test_register_and_route(self):
        router = MessageRouter()
        router.register_agent("birch")
        msg = router.route("volt", "birch", "question", {"text": "Hello"})
        assert msg.status == RouteStatus.PENDING

    def test_route_to_unregistered_agent(self):
        router = MessageRouter()
        msg = router.route("volt", "ghost", "hello")
        assert msg.status == RouteStatus.DEAD_LETTER
        assert "Unknown" in msg.error

    def test_route_rejected_type(self):
        router = MessageRouter()
        router.register_agent("birch", accepted_types={"meditation"})
        msg = router.route("volt", "birch", "combat")
        assert msg.status == RouteStatus.REJECTED
        assert "does not accept" in msg.error

    def test_route_accepted_type(self):
        router = MessageRouter()
        router.register_agent("birch", accepted_types={"question"})
        msg = router.route("volt", "birch", "question")
        assert msg.status == RouteStatus.PENDING

    def test_route_all_types_when_empty_filter(self):
        router = MessageRouter()
        router.register_agent("nyx")  # No filter = accepts all
        msg = router.route("volt", "nyx", "anything_at_all")
        assert msg.status == RouteStatus.PENDING

    def test_collect_delivers_messages(self):
        router = MessageRouter()
        router.register_agent("birch")
        router.route("volt", "birch", "question", {"n": 1})
        router.route("volt", "birch", "question", {"n": 2})

        collected = router.collect("birch")
        assert len(collected) == 2
        assert all(m.status == RouteStatus.DELIVERED for m in collected)
        assert all(m.delivered_at is not None for m in collected)

    def test_collect_only_returns_target_messages(self):
        router = MessageRouter()
        router.register_agent("birch")
        router.register_agent("nyx")
        router.route("volt", "birch", "question")
        router.route("volt", "nyx", "paradox")

        birch_msgs = router.collect("birch")
        assert len(birch_msgs) == 1
        assert birch_msgs[0].target_agent == "birch"
        assert router.get_pending_count("nyx") == 1

    def test_collect_empties_queue(self):
        router = MessageRouter()
        router.register_agent("target")
        router.route("source", "target", "ping")
        router.collect("target")
        assert router.get_pending_count("target") == 0

    def test_unregister_agent(self):
        router = MessageRouter()
        router.register_agent("temp")
        router.unregister_agent("temp")
        msg = router.route("volt", "temp", "hello")
        assert msg.status == RouteStatus.DEAD_LETTER


# ---------------------------------------------------------------------------
# Broadcast tests
# ---------------------------------------------------------------------------

class TestBroadcast:
    """Test broadcast messaging."""

    def test_broadcast_to_all(self):
        router = MessageRouter()
        router.register_agent("volt")
        router.register_agent("birch")
        router.register_agent("nyx")

        messages = router.broadcast("rust", "announcement", {"text": "Hello all"})
        targets = {m.target_agent for m in messages}
        assert targets == {"volt", "birch", "nyx"}

    def test_broadcast_excludes_source(self):
        router = MessageRouter()
        router.register_agent("volt")
        router.register_agent("birch")

        messages = router.broadcast("volt", "status_update")
        targets = {m.target_agent for m in messages}
        assert "volt" not in targets

    def test_broadcast_with_explicit_exclusion(self):
        router = MessageRouter()
        router.register_agent("volt")
        router.register_agent("birch")
        router.register_agent("nyx")

        messages = router.broadcast("rust", "secret", exclude={"nyx"})
        targets = {m.target_agent for m in messages}
        assert "nyx" not in targets
        assert "rust" not in targets

    def test_broadcast_respects_type_filter(self):
        router = MessageRouter()
        router.register_agent("volt", accepted_types={"alert"})
        router.register_agent("birch", accepted_types={"meditation"})

        messages = router.broadcast("admin", "alert")
        # broadcast() only sends to agents that accept the type — birch is skipped
        assert len(messages) == 1
        assert messages[0].target_agent == "volt"
        assert messages[0].status == RouteStatus.PENDING


# ---------------------------------------------------------------------------
# Queue management tests
# ---------------------------------------------------------------------------

class TestQueueManagement:
    """Test queue overflow and expiration."""

    def test_queue_overflow_rejected(self):
        router = MessageRouter()
        router.register_agent("small", max_queue_size=3)
        for i in range(3):
            msg = router.route("source", "small", "msg", {"n": i})
            assert msg.status == RouteStatus.PENDING
        overflow = router.route("source", "small", "msg", {"n": 3})
        assert overflow.status == RouteStatus.REJECTED
        assert "queue full" in overflow.error.lower()

    def test_expired_messages_to_dead_letters(self):
        router = MessageRouter()
        router.register_agent("lazy")
        router.route("source", "lazy", "urgent", ttl_seconds=0.0)
        time.sleep(0.01)  # Ensure expiry
        collected = router.collect("lazy")
        assert len(collected) == 0
        assert len(router.get_dead_letters()) == 1

    def test_cleanup_expired(self):
        router = MessageRouter()
        router.register_agent("target")
        router.route("source", "target", "msg", ttl_seconds=0.0)
        time.sleep(0.01)
        cleaned = router.cleanup_expired()
        assert cleaned == 1
        assert router.get_pending_count() == 0


# ---------------------------------------------------------------------------
# Statistics tests
# ---------------------------------------------------------------------------

class TestRoutingStats:
    """Test routing statistics."""

    def test_stats_structure(self):
        router = MessageRouter()
        router.register_agent("volt")
        router.register_agent("birch", accepted_types={"question"})
        router.route("nyx", "volt", "hello")
        router.route("nyx", "birch", "question")

        stats = router.get_stats()
        assert stats["total_routed"] == 2
        assert stats["pending"] == 2
        assert stats["registered_agents"] == 2
        assert "volt" in stats["agents"]
        assert "birch" in stats["agents"]

    def test_stats_after_delivery(self):
        router = MessageRouter()
        router.register_agent("target")
        router.route("source", "target", "ping")
        router.collect("target")

        stats = router.get_stats()
        assert stats["delivered"] == 1
        assert stats["pending"] == 0

    def test_dead_letter_count(self):
        router = MessageRouter()
        router.route("source", "nobody", "hello")
        router.route("source", "nobody", "hello")

        stats = router.get_stats()
        assert stats["dead_letters"] == 2


# ---------------------------------------------------------------------------
# Audit integration
# ---------------------------------------------------------------------------

class TestRoutingAudit:
    """Test routing audit logging."""

    def test_successful_route_logged(self):
        audit = MagicMock()
        router = MessageRouter(audit_log=audit)
        router.register_agent("target")
        router.route("source", "target", "hello")
        audit.log.assert_called_once()
        kwargs = audit.log.call_args[1]
        assert kwargs["success"] is True
        assert kwargs["action"] == "message_route"

    def test_dead_letter_logged_as_failure(self):
        audit = MagicMock()
        router = MessageRouter(audit_log=audit)
        router.route("source", "ghost", "hello")
        kwargs = audit.log.call_args[1]
        assert kwargs["success"] is False

    def test_no_audit_is_fine(self):
        """Router works without audit log."""
        router = MessageRouter()
        router.register_agent("target")
        msg = router.route("source", "target", "hello")
        assert msg.status == RouteStatus.PENDING
