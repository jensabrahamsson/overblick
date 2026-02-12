"""
Intra-plugin event bus — lightweight pub/sub.

Allows plugins to communicate without direct dependencies.
Events are fire-and-forget async; errors in handlers don't propagate to emitters.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """
    Simple async event bus for intra-plugin communication.

    Usage:
        bus = EventBus()
        bus.subscribe("post.created", my_handler)
        await bus.emit("post.created", post_id="abc", title="Hello")
    """

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._handler_count = 0

    def subscribe(self, event: str, handler: EventHandler) -> None:
        """
        Subscribe to an event.

        Args:
            event: Event name (e.g. "post.created", "challenge.detected")
            handler: Async callable to invoke when event fires
        """
        self._handlers[event].append(handler)
        self._handler_count += 1
        logger.debug(f"EventBus: subscribed to '{event}' (total: {self._handler_count})")

    def unsubscribe(self, event: str, handler: EventHandler) -> bool:
        """
        Unsubscribe from an event.

        Returns:
            True if handler was found and removed
        """
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)
            self._handler_count -= 1
            return True
        return False

    async def emit(self, event: str, **kwargs: Any) -> int:
        """
        Emit an event to all subscribers.

        Handlers run concurrently. Errors are logged but don't propagate.

        Args:
            event: Event name
            **kwargs: Event data passed to handlers

        Returns:
            Number of handlers that executed successfully
        """
        handlers = self._handlers.get(event, [])
        if not handlers:
            return 0

        results = await asyncio.gather(
            *[self._safe_call(h, event, **kwargs) for h in handlers],
            return_exceptions=True,
        )

        success = sum(1 for r in results if r is True)
        failures = sum(1 for r in results if r is not True)

        if failures:
            logger.warning(f"EventBus: '{event}' — {success} ok, {failures} failed")

        return success

    async def _safe_call(self, handler: EventHandler, event: str, **kwargs: Any) -> bool:
        """Call handler with error isolation."""
        try:
            await handler(**kwargs)
            return True
        except Exception as e:
            logger.error(f"EventBus: handler error on '{event}': {e}", exc_info=True)
            return False

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()
        self._handler_count = 0

    @property
    def subscription_count(self) -> int:
        """Total number of active subscriptions."""
        return self._handler_count
