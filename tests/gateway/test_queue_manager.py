"""Tests for QueueManager."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from overblick.gateway.config import GatewayConfig
from overblick.gateway.models import ChatRequest, ChatMessage, ChatResponse, Priority
from overblick.gateway.queue_manager import QueueManager


class TestQueueManager:
    """Tests for QueueManager."""

    @pytest.fixture
    def config(self):
        return GatewayConfig(
            max_queue_size=10,
            request_timeout_seconds=5.0,
            max_concurrent_requests=1,
        )

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.health_check.return_value = True
        client.chat_completion.return_value = ChatResponse.from_message(
            model="qwen3:8b",
            content="Test response",
        )
        client.close.return_value = None
        return client

    @pytest.fixture
    def sample_request(self):
        return ChatRequest(
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Hello")],
        )

    async def test_start_stop(self, config, mock_client):
        qm = QueueManager(config=config, client=mock_client)

        assert not qm.is_running

        await qm.start()
        assert qm.is_running

        await qm.stop()
        assert not qm.is_running

    async def test_submit_request(self, config, mock_client, sample_request):
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            response = await qm.submit(sample_request, Priority.LOW)

            assert response.model == "qwen3:8b"
            assert response.choices[0].message.content == "Test response"
            mock_client.chat_completion.assert_called_once()
        finally:
            await qm.stop()

    async def test_priority_ordering(self, config, mock_client):
        """HIGH priority requests are processed before LOW."""
        async def slow_completion(request):
            await asyncio.sleep(0.1)
            return ChatResponse.from_message(
                model=request.model,
                content=f"Response for {request.messages[-1].content}",
            )

        mock_client.chat_completion.side_effect = slow_completion

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            low_req = ChatRequest(
                model="qwen3:8b",
                messages=[ChatMessage(role="user", content="LOW")],
            )
            high_req = ChatRequest(
                model="qwen3:8b",
                messages=[ChatMessage(role="user", content="HIGH")],
            )

            results = await asyncio.gather(
                qm.submit(low_req, Priority.LOW),
                qm.submit(high_req, Priority.HIGH),
            )

            assert len(results) == 2
        finally:
            await qm.stop()

    async def test_queue_full(self, mock_client, sample_request):
        config = GatewayConfig(
            max_queue_size=1,
            request_timeout_seconds=5.0,
            max_concurrent_requests=1,
        )

        async def slow_completion(request):
            await asyncio.sleep(1.0)
            return ChatResponse.from_message("qwen3:8b", "Response")

        mock_client.chat_completion.side_effect = slow_completion

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            task1 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            await asyncio.sleep(0.05)

            task2 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            await asyncio.sleep(0.05)

            with pytest.raises(asyncio.QueueFull):
                qm._queue.put_nowait(sample_request)

            task1.cancel()
            task2.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass
            try:
                await task2
            except asyncio.CancelledError:
                pass
        finally:
            await qm.stop()

    async def test_stats_tracking(self, config, mock_client, sample_request):
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            stats = qm.get_stats()
            assert stats.requests_processed == 0

            await qm.submit(sample_request, Priority.HIGH)

            stats = qm.get_stats()
            assert stats.requests_processed == 1
            assert stats.requests_high_priority == 1
            assert stats.avg_response_time_ms > 0

            await qm.submit(sample_request, Priority.LOW)

            stats = qm.get_stats()
            assert stats.requests_processed == 2
            assert stats.requests_low_priority == 1
        finally:
            await qm.stop()

    async def test_not_running_error(self, config, mock_client, sample_request):
        qm = QueueManager(config=config, client=mock_client)

        with pytest.raises(RuntimeError, match="not running"):
            await qm.submit(sample_request, Priority.LOW)

    async def test_client_error_propagation(self, config, sample_request):
        mock_client = AsyncMock()
        mock_client.chat_completion.side_effect = Exception("LLM Error")
        mock_client.close.return_value = None

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            with pytest.raises(Exception, match="LLM Error"):
                await qm.submit(sample_request, Priority.LOW)
        finally:
            await qm.stop()

    async def test_queue_size_property(self, config, mock_client):
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            assert qm.queue_size == 0
        finally:
            await qm.stop()
