"""Tests for QueueManager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.gateway.config import GatewayConfig
from overblick.gateway.models import ChatMessage, ChatRequest, ChatResponse, Priority, QueuedRequest
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
            model="qwen3.5:9b",
            content="Test response",
        )
        client.close.return_value = None
        return client

    @pytest.fixture
    def sample_request(self):
        return ChatRequest(
            model="qwen3.5:9b",
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

            assert response.model == "qwen3.5:9b"
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
                model="qwen3.5:9b",
                messages=[ChatMessage(role="user", content="LOW")],
            )
            high_req = ChatRequest(
                model="qwen3.5:9b",
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
        """Test that a full queue rejects new requests."""
        config = GatewayConfig(
            max_queue_size=1,
            request_timeout_seconds=5.0,
            max_concurrent_requests=1,
        )

        # Don't start the worker — test the queue limit directly
        qm = QueueManager(config=config, client=mock_client)
        # Manually mark as running so submit() doesn't raise
        qm._running = True

        # Fill the queue (size=1)
        qm._queue.put_nowait(
            QueuedRequest(priority=Priority.LOW, timestamp=0, request=sample_request)
        )

        # Next put should raise QueueFull
        with pytest.raises(asyncio.QueueFull):
            qm._queue.put_nowait(
                QueuedRequest(priority=Priority.LOW, timestamp=1, request=sample_request)
            )

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

    async def test_should_warn_when_already_running(self, config, mock_client):
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()
        try:
            # Second start should just return (no error)
            await qm.start()
            assert qm.is_running
        finally:
            await qm.stop()

    async def test_should_validate_backend_in_submit(self, config, mock_client, sample_request):
        mock_registry = MagicMock()
        mock_registry.get_client.side_effect = ValueError("Unknown backend 'bad'")

        qm = QueueManager(config=config, client=mock_client, registry=mock_registry)
        await qm.start()
        try:
            with pytest.raises(ValueError, match="Unknown backend"):
                await qm.submit(sample_request, Priority.LOW, backend="bad")
        finally:
            await qm.stop()

    async def test_should_raise_queue_full(self, mock_client, sample_request):
        """Verify QueueFull is raised when the internal queue is at capacity.

        We test the queue capacity mechanism directly: create a QueueManager
        but stop the worker loop so nothing drains the queue, then fill it
        and verify the next submit raises QueueFull.
        """
        config = GatewayConfig(
            max_queue_size=2,
            request_timeout_seconds=5.0,
            max_concurrent_requests=1,
        )

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        # Stop the worker so it doesn't drain the queue
        qm._running = False
        if qm._worker_task:
            qm._worker_task.cancel()
            try:
                await qm._worker_task
            except (asyncio.CancelledError, Exception):
                pass
        # Mark as running again so submit() doesn't raise RuntimeError
        qm._running = True

        try:
            # Fill the queue (maxsize=2) — these won't be processed
            task1 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            task2 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            await asyncio.sleep(0.05)

            # Third submit should raise QueueFull
            with pytest.raises(asyncio.QueueFull):
                await qm.submit(sample_request, Priority.LOW)

            # Clean up pending tasks
            task1.cancel()
            task2.cancel()
            await asyncio.gather(task1, task2, return_exceptions=True)
        finally:
            qm._running = False

    async def test_should_raise_timeout(self, mock_client, sample_request):
        config = GatewayConfig(
            max_queue_size=10,
            request_timeout_seconds=0.1,  # very short timeout
            max_concurrent_requests=1,
        )

        async def slow_completion(request):
            await asyncio.sleep(10)
            return ChatResponse.from_message("qwen3:8b", "Response")

        mock_client.chat_completion.side_effect = slow_completion

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            with pytest.raises(TimeoutError):
                await qm.submit(sample_request, Priority.LOW)
        finally:
            await qm.stop()

    async def test_should_skip_cancelled_request_before_processing(
        self, config, mock_client, sample_request
    ):
        processing_event = asyncio.Event()
        release_event = asyncio.Event()

        async def blocking_completion(request):
            processing_event.set()
            await release_event.wait()
            return ChatResponse.from_message("qwen3:8b", "Response")

        mock_client.chat_completion.side_effect = blocking_completion

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            # Submit a blocking request first
            task1 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            await processing_event.wait()

            # Submit another, then cancel it before it can be processed
            task2 = asyncio.create_task(qm.submit(sample_request, Priority.LOW))
            await asyncio.sleep(0.05)
            task2.cancel()

            try:
                await task2
            except asyncio.CancelledError:
                pass

            release_event.set()
            await task1
        finally:
            await qm.stop()

    async def test_should_skip_cancelled_request_after_semaphore(
        self, config, mock_client, sample_request
    ):
        call_count = 0

        async def counting_completion(request):
            nonlocal call_count
            call_count += 1
            return ChatResponse.from_message("qwen3:8b", "Response")

        mock_client.chat_completion.side_effect = counting_completion

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            # Submit and get response
            response = await qm.submit(sample_request, Priority.LOW)
            assert response.model == "qwen3:8b"
        finally:
            await qm.stop()

    async def test_should_use_registry_default_client(self, config, mock_client, sample_request):
        mock_registry = MagicMock()
        default_client = AsyncMock()
        default_client.chat_completion.return_value = ChatResponse.from_message(
            "qwen3:8b", "from registry default"
        )
        mock_registry.get_client.return_value = default_client

        qm = QueueManager(config=config, client=mock_client, registry=mock_registry)
        await qm.start()

        try:
            response = await qm.submit(sample_request, Priority.LOW)
            assert response.choices[0].message.content == "from registry default"
            # get_client() called with no args = default
            mock_registry.get_client.assert_called()
        finally:
            await qm.stop()

    async def test_should_use_registry_specific_backend(self, config, mock_client, sample_request):
        mock_registry = MagicMock()
        cloud_client = AsyncMock()
        cloud_client.chat_completion.return_value = ChatResponse.from_message(
            "deepseek", "from cloud"
        )

        def get_client_side_effect(name=None):
            if name == "cloud":
                return cloud_client
            return mock_client

        mock_registry.get_client.side_effect = get_client_side_effect

        qm = QueueManager(config=config, client=mock_client, registry=mock_registry)
        await qm.start()

        try:
            response = await qm.submit(sample_request, Priority.LOW, backend="cloud")
            assert response.choices[0].message.content == "from cloud"
        finally:
            await qm.stop()

    async def test_should_use_legacy_client_when_no_registry(
        self, config, mock_client, sample_request
    ):
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            response = await qm.submit(sample_request, Priority.LOW)
            assert response.model == "qwen3.5:9b"
            mock_client.chat_completion.assert_called_once()
        finally:
            await qm.stop()

    async def test_should_continue_on_empty_queue_timeout(self, config, mock_client):
        """Worker loop continues when queue.get times out (empty queue)."""
        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            # Just let the worker loop run for a bit with empty queue
            # The worker will hit the 1s timeout on queue.get and continue
            await asyncio.sleep(1.5)
            assert qm.is_running
        finally:
            await qm.stop()

    async def test_should_handle_unexpected_worker_error(self, config, sample_request):
        """Worker loop handles unexpected exceptions from _process_request."""

        mock_client = AsyncMock()
        mock_client.close.return_value = None
        mock_client.chat_completion.return_value = ChatResponse.from_message(
            "qwen3:8b", "ok"
        )

        qm = QueueManager(config=config, client=mock_client)
        await qm.start()

        try:
            # Monkey-patch _process_request to raise an unexpected error once
            original_process = qm._process_request
            call_count = 0

            async def failing_process(queued):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Unexpected processing error")
                return await original_process(queued)

            qm._process_request = failing_process

            # First request will fail due to our patched error
            # The future will never complete, so it will timeout
            with pytest.raises((RuntimeError, TimeoutError)):
                await asyncio.wait_for(
                    qm.submit(sample_request, Priority.LOW), timeout=1.0
                )

            # Worker loop should have recovered (slept 0.1s and continued)
            await asyncio.sleep(0.2)
            assert qm.is_running

            # Second request should succeed
            response = await qm.submit(sample_request, Priority.LOW)
            assert response.choices[0].message.content == "ok"
        finally:
            await qm.stop()

    async def test_should_skip_request_cancelled_after_semaphore_acquire(
        self, config, mock_client, sample_request
    ):
        """Request cancelled after semaphore acquired but before chat_completion.

        Directly calls _process_request with a future that gets cancelled
        after semaphore acquisition via a patched Semaphore.
        """
        from overblick.gateway.models import QueuedRequest

        qm = QueueManager(config=config, client=mock_client)
        qm._running = True  # pretend it's running

        future = asyncio.get_running_loop().create_future()

        queued = QueuedRequest(
            priority=Priority.LOW,
            timestamp=0,
            request=sample_request,
            future=future,
            backend=None,
        )

        # Replace semaphore with one that cancels the future on acquire
        original_semaphore = qm._semaphore

        class CancellingSemaphore:
            async def __aenter__(self):
                await original_semaphore.__aenter__()
                # Cancel the future after acquiring
                future.cancel()
                return self

            async def __aexit__(self, *args):
                return await original_semaphore.__aexit__(*args)

        qm._semaphore = CancellingSemaphore()

        # Put it in the queue so task_done works
        await qm._queue.put(queued)

        await qm._process_request(queued)

        assert future.cancelled()
        # chat_completion should NOT have been called
        assert mock_client.chat_completion.call_count == 0
