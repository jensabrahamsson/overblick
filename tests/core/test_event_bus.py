"""Tests for event bus."""

import pytest
from overblick.core.event_bus import EventBus


@pytest.mark.asyncio
async def test_subscribe_and_emit():
    bus = EventBus()
    received = []

    async def handler(**kwargs):
        received.append(kwargs)

    bus.subscribe("test.event", handler)
    await bus.emit("test.event", key="value")

    assert len(received) == 1
    assert received[0] == {"key": "value"}


@pytest.mark.asyncio
async def test_multiple_handlers():
    bus = EventBus()
    results = []

    async def handler1(**kwargs):
        results.append("h1")

    async def handler2(**kwargs):
        results.append("h2")

    bus.subscribe("evt", handler1)
    bus.subscribe("evt", handler2)
    await bus.emit("evt")

    assert "h1" in results
    assert "h2" in results


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(**kwargs):
        received.append(kwargs)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    await bus.emit("evt", x=1)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_no_handlers():
    bus = EventBus()
    result = await bus.emit("nonexistent")
    assert result == 0


@pytest.mark.asyncio
async def test_handler_error_isolation():
    bus = EventBus()
    results = []

    async def bad_handler(**kwargs):
        raise ValueError("boom")

    async def good_handler(**kwargs):
        results.append("ok")

    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    await bus.emit("evt")

    assert "ok" in results
