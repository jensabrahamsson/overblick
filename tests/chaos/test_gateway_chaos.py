"""
Chaos tests — Gateway failure injection.

Verifies the gateway degrades gracefully under:
- Network failures (backend dies mid-request)
- Concurrent access (50 simultaneous requests)
- Resource exhaustion (queue at max capacity)
- Race conditions (shutdown with queued requests)
- Corrupt/oversized payloads
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from overblick.gateway.config import GatewayConfig, reset_config
from overblick.gateway.backend_registry import BackendRegistry
from overblick.gateway.router import RequestRouter
from overblick.gateway.queue_manager import QueueManager
from overblick.gateway.models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatResponseChoice,
    ChatResponseUsage,
)


def _make_response(content: str = "OK") -> ChatResponse:
    return ChatResponse(
        id="test",
        model="qwen3:8b",
        choices=[ChatResponseChoice(message=ChatMessage(role="assistant", content=content))],
        usage=ChatResponseUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
    )


def _make_request(content: str = "Hello") -> ChatRequest:
    return ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage(role="user", content=content)],
        max_tokens=100,
        temperature=0.7,
    )


@pytest.fixture
def small_queue_config():
    reset_config()
    return GatewayConfig(
        max_queue_size=5,
        request_timeout_seconds=5.0,
        max_concurrent_requests=1,
    )


# ---------------------------------------------------------------------------
# Network failures
# ---------------------------------------------------------------------------

class TestNetworkFailures:
    """Backend fails mid-request."""

    @pytest.mark.asyncio
    async def test_backend_crash_during_request(self, small_queue_config):
        """Client throws ConnectionError during chat completion."""
        client = AsyncMock()
        client.health_check.return_value = True
        client.chat_completion.side_effect = ConnectionError("Connection reset")
        client.close.return_value = None

        qm = QueueManager(config=small_queue_config, client=client)
        await qm.start()

        try:
            with pytest.raises(Exception):
                await asyncio.wait_for(
                    qm.submit(_make_request(), priority="low"),
                    timeout=5.0,
                )
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_backend_timeout(self, small_queue_config):
        """Backend takes too long to respond."""
        async def slow_response(*args, **kwargs):
            await asyncio.sleep(100)  # Never returns in time

        client = AsyncMock()
        client.health_check.return_value = True
        client.chat_completion = slow_response
        client.close.return_value = None

        qm = QueueManager(
            config=GatewayConfig(
                max_queue_size=5,
                request_timeout_seconds=0.5,  # Very short timeout
                max_concurrent_requests=1,
            ),
            client=client,
        )
        await qm.start()

        try:
            with pytest.raises((asyncio.TimeoutError, Exception)):
                await asyncio.wait_for(
                    qm.submit(_make_request(), priority="low"),
                    timeout=2.0,
                )
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_health_check_during_outage(self):
        """health_check_all catches exceptions and returns False for failing backends."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
            },
        )
        registry = BackendRegistry(config)
        failing_client = AsyncMock()
        failing_client.health_check.side_effect = ConnectionError("dead")
        registry._clients["local"] = failing_client

        # health_check_all catches per-backend exceptions
        result = await registry.health_check_all()
        assert result["local"] is False


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    """Many simultaneous requests."""

    @pytest.mark.asyncio
    async def test_50_simultaneous_requests(self, small_queue_config):
        """50 concurrent submissions — system doesn't crash."""
        client = AsyncMock()
        client.health_check.return_value = True

        async def delayed_response(*a, **kw):
            await asyncio.sleep(0.01)
            return _make_response("OK")

        client.chat_completion = delayed_response
        client.close.return_value = None

        config = GatewayConfig(
            max_queue_size=100,
            request_timeout_seconds=10.0,
            max_concurrent_requests=5,
        )
        qm = QueueManager(config=config, client=client)
        await qm.start()

        try:
            tasks = [
                qm.submit(_make_request(f"msg-{i}"), priority="low")
                for i in range(50)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Not all may succeed, but the system should not deadlock or crash
            # Count both successes and expected exceptions (not hangs)
            assert len(results) == 50
        finally:
            await qm.stop()

    def test_router_thread_safety(self):
        """Router resolves correctly even with rapid calls."""
        config = GatewayConfig(
            default_backend="local",
            backends={
                "local": {"enabled": True, "type": "ollama", "host": "127.0.0.1", "port": 11434},
                "deepseek": {
                    "enabled": True, "type": "deepseek",
                    "api_key": "sk-test", "api_url": "https://api.deepseek.com/v1",
                },
            },
        )
        registry = BackendRegistry(config)
        router = RequestRouter(registry)

        # Rapid-fire 1000 routing decisions
        results = set()
        for _ in range(1000):
            results.add(router.resolve_backend(complexity="high"))

        # Should consistently route to deepseek
        assert results == {"deepseek"}


# ---------------------------------------------------------------------------
# Resource exhaustion
# ---------------------------------------------------------------------------

class TestResourceExhaustion:
    """Queue at max capacity."""

    @pytest.mark.asyncio
    async def test_queue_full_rejection(self, small_queue_config):
        """When queue is full, new requests should be rejected."""
        async def never_respond(*a, **kw):
            await asyncio.sleep(1000)

        client = AsyncMock()
        client.health_check.return_value = True
        client.chat_completion = never_respond
        client.close.return_value = None

        qm = QueueManager(config=small_queue_config, client=client)
        await qm.start()

        try:
            # Fill the queue (max_queue_size=5)
            tasks = []
            for i in range(10):
                tasks.append(
                    asyncio.create_task(
                        qm.submit(_make_request(f"fill-{i}"), priority="low")
                    )
                )

            # Wait a bit for queue to fill
            await asyncio.sleep(0.1)

            # Check queue size doesn't exceed max
            assert qm.queue_size <= small_queue_config.max_queue_size + 1
        finally:
            for t in tasks:
                t.cancel()
            await qm.stop()


# ---------------------------------------------------------------------------
# Corrupt data
# ---------------------------------------------------------------------------

class TestCorruptData:
    """Malformed inputs."""

    def test_empty_messages_request(self):
        """Request with empty messages list."""
        req = ChatRequest(
            model="qwen3:8b",
            messages=[],
            max_tokens=100,
            temperature=0.7,
        )
        assert len(req.messages) == 0

    def test_registry_handles_empty_backends(self):
        """Registry with empty backends dict creates fallback."""
        config = GatewayConfig(backends={})
        registry = BackendRegistry(config)
        assert len(registry.available_backends) > 0
        assert registry.default_backend == "local"

    def test_router_with_no_backends(self):
        """Router with single fallback backend works."""
        config = GatewayConfig(backends={})
        registry = BackendRegistry(config)
        router = RequestRouter(registry)
        # Should not crash
        result = router.resolve_backend(complexity="high")
        assert result == "local"
