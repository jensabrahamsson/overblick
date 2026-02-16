"""
Integration tests — Event bus cross-plugin communication.

Tests that the event bus correctly delivers events between multiple
subscribers, handles errors in handlers without affecting others,
and supports event chains (handler A emits → handler B receives).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from overblick.core.event_bus import EventBus


class TestMultipleSubscribers:
    """Multiple handlers subscribed to the same event."""

    @pytest.mark.asyncio
    async def test_all_subscribers_receive_event(self):
        """Three handlers subscribed to same event all get invoked."""
        bus = EventBus()
        results = []

        async def handler_a(**kwargs):
            results.append(("a", kwargs))

        async def handler_b(**kwargs):
            results.append(("b", kwargs))

        async def handler_c(**kwargs):
            results.append(("c", kwargs))

        bus.subscribe("post.created", handler_a)
        bus.subscribe("post.created", handler_b)
        bus.subscribe("post.created", handler_c)

        await bus.emit("post.created", author="anomal", content="Hello world")

        assert len(results) == 3
        handlers = {r[0] for r in results}
        assert handlers == {"a", "b", "c"}
        # All received the same payload
        for _, kwargs in results:
            assert kwargs["author"] == "anomal"
            assert kwargs["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_no_subscribers_emits_safely(self):
        """Emitting an event with no subscribers does not raise."""
        bus = EventBus()
        await bus.emit("orphan.event", data="ignored")

    @pytest.mark.asyncio
    async def test_different_events_isolated(self):
        """Handlers only fire for their subscribed event."""
        bus = EventBus()
        results = []

        async def on_post(**kwargs):
            results.append("post")

        async def on_comment(**kwargs):
            results.append("comment")

        bus.subscribe("post.created", on_post)
        bus.subscribe("comment.created", on_comment)

        await bus.emit("post.created")
        assert results == ["post"]

        await bus.emit("comment.created")
        assert results == ["post", "comment"]


class TestHandlerErrorIsolation:
    """Errors in one handler don't block others."""

    @pytest.mark.asyncio
    async def test_error_in_handler_does_not_block_others(self):
        """If handler A raises, handler B still executes."""
        bus = EventBus()
        results = []

        async def failing_handler(**kwargs):
            raise ValueError("Handler A exploded")

        async def succeeding_handler(**kwargs):
            results.append("success")

        bus.subscribe("test.event", failing_handler)
        bus.subscribe("test.event", succeeding_handler)

        # Should not raise
        await bus.emit("test.event")

        # Handler B executed despite handler A's failure
        assert "success" in results


class TestEventChaining:
    """Handler emitting a new event triggers downstream handlers."""

    @pytest.mark.asyncio
    async def test_handler_chain_a_to_b_to_c(self):
        """Event chain: post.created → scoring.complete → action.queued."""
        bus = EventBus()
        results = []

        async def on_post_created(**kwargs):
            results.append("step1_post")
            await bus.emit("scoring.complete", score=0.8, post_id=kwargs.get("post_id"))

        async def on_scoring_complete(**kwargs):
            results.append("step2_scoring")
            if kwargs.get("score", 0) > 0.5:
                await bus.emit("action.queued", action="comment", post_id=kwargs.get("post_id"))

        async def on_action_queued(**kwargs):
            results.append(f"step3_action_{kwargs.get('action')}")

        bus.subscribe("post.created", on_post_created)
        bus.subscribe("scoring.complete", on_scoring_complete)
        bus.subscribe("action.queued", on_action_queued)

        await bus.emit("post.created", post_id=42)

        assert results == ["step1_post", "step2_scoring", "step3_action_comment"]


class TestUnsubscribe:
    """Handlers can be removed and stop receiving events."""

    @pytest.mark.asyncio
    async def test_unsubscribed_handler_not_called(self):
        """After unsubscribe, handler no longer receives events."""
        bus = EventBus()
        results = []

        async def handler_a(**kwargs):
            results.append("a")

        async def handler_b(**kwargs):
            results.append("b")

        bus.subscribe("test.event", handler_a)
        bus.subscribe("test.event", handler_b)

        await bus.emit("test.event")
        assert results == ["a", "b"]

        bus.unsubscribe("test.event", handler_a)

        results.clear()
        await bus.emit("test.event")
        assert results == ["b"]


class TestConcurrentEmits:
    """Multiple emit() calls don't interfere with each other."""

    @pytest.mark.asyncio
    async def test_concurrent_emits_all_handled(self):
        """Three concurrent emits all complete without interference."""
        bus = EventBus()
        results = []

        async def slow_handler(**kwargs):
            await asyncio.sleep(0.01)
            results.append(kwargs.get("id"))

        bus.subscribe("concurrent.test", slow_handler)

        await asyncio.gather(
            bus.emit("concurrent.test", id=1),
            bus.emit("concurrent.test", id=2),
            bus.emit("concurrent.test", id=3),
        )

        assert sorted(results) == [1, 2, 3]


class TestEventBusWithPluginSimulation:
    """Simulate real plugin communication patterns."""

    @pytest.mark.asyncio
    async def test_email_notify_telegram_flow(self):
        """Email plugin classifies email → emits → Telegram sends notification."""
        bus = EventBus()
        notifications_sent = []

        # Simulated Telegram handler
        async def telegram_notification_handler(**kwargs):
            notifications_sent.append({
                "to": kwargs.get("chat_id"),
                "text": f"New email from {kwargs.get('sender')}: {kwargs.get('subject')}",
            })

        bus.subscribe("email.classified", telegram_notification_handler)

        # Simulated Email Agent emits classification
        await bus.emit(
            "email.classified",
            sender="alice@example.com",
            subject="Meeting tomorrow",
            classification="notify",
            chat_id="12345",
        )

        assert len(notifications_sent) == 1
        assert "alice@example.com" in notifications_sent[0]["text"]
        assert "Meeting tomorrow" in notifications_sent[0]["text"]

    @pytest.mark.asyncio
    async def test_moltbook_post_triggers_engagement_tracking(self):
        """Moltbook posts a comment → event → engagement tracked."""
        bus = EventBus()
        engagements = []

        async def track_engagement(**kwargs):
            engagements.append({
                "agent": kwargs.get("agent"),
                "action": kwargs.get("action"),
                "target_id": kwargs.get("target_id"),
            })

        bus.subscribe("agent.action", track_engagement)

        await bus.emit(
            "agent.action",
            agent="cherry",
            action="comment",
            target_id="post_12345",
        )

        assert len(engagements) == 1
        assert engagements[0]["agent"] == "cherry"
        assert engagements[0]["action"] == "comment"
