"""
Priority queue manager for LLM requests.

Implements a priority queue where HIGH priority requests (interactive identity
agents) are processed before LOW priority requests (background tasks).
A single worker serializes GPU access.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from .config import GatewayConfig, get_config
from .models import ChatRequest, ChatResponse, Priority, QueuedRequest, GatewayStats
from .ollama_client import OllamaClient

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Manages the priority queue and worker for LLM requests.

    Features:
    - Priority-based ordering (HIGH=1, LOW=5)
    - FIFO within same priority level
    - Single worker to protect GPU
    - Metrics and statistics tracking
    """

    def __init__(
        self,
        config: Optional[GatewayConfig] = None,
        client: Optional[OllamaClient] = None,
    ):
        """Initialize the queue manager."""
        self.config = config or get_config()
        self.client = client or OllamaClient(self.config)

        self._queue: asyncio.PriorityQueue[QueuedRequest] = asyncio.PriorityQueue(
            maxsize=self.config.max_queue_size
        )

        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)

        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        self._stats = {
            "requests_processed": 0,
            "requests_high": 0,
            "requests_low": 0,
            "total_response_time_ms": 0.0,
        }
        self._start_time = time.time()
        self._is_processing = False

        # Recent response times for averaging (last 100)
        self._response_times: deque[float] = deque(maxlen=100)

    async def start(self) -> None:
        """Start the queue worker."""
        if self._running:
            logger.warning("Queue manager already running")
            return

        self._running = True
        self._start_time = time.time()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Queue manager started")

    async def stop(self) -> None:
        """Stop the queue worker gracefully."""
        self._running = False

        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        await self.client.close()
        logger.info("Queue manager stopped")

    async def submit(
        self,
        request: ChatRequest,
        priority: Priority = Priority.LOW,
    ) -> ChatResponse:
        """
        Submit a request to the queue and wait for completion.

        Args:
            request: The chat request
            priority: Request priority (HIGH or LOW)

        Returns:
            The chat response

        Raises:
            asyncio.QueueFull: If queue is at capacity
            asyncio.TimeoutError: If request times out
            OllamaError: If LLM request fails
        """
        if not self._running:
            raise RuntimeError("Queue manager not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ChatResponse] = loop.create_future()

        queued = QueuedRequest(
            priority=priority,
            timestamp=time.time(),
            request=request,
            future=future,
        )

        logger.debug("Submitting request %s with priority %s", queued.request_id, priority.name)

        try:
            self._queue.put_nowait(queued)
        except asyncio.QueueFull:
            logger.warning("Queue full, rejecting request %s", queued.request_id)
            raise

        try:
            response = await asyncio.wait_for(
                future,
                timeout=self.config.request_timeout_seconds,
            )
            return response
        except asyncio.TimeoutError:
            logger.error("Request %s timed out", queued.request_id, exc_info=True)
            raise

    async def _worker_loop(self) -> None:
        """Main worker loop that processes queued requests."""
        logger.info("Worker loop started")

        while self._running:
            try:
                try:
                    queued = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                await self._process_request(queued)

            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e, exc_info=True)
                await asyncio.sleep(0.1)

        logger.info("Worker loop stopped")

    async def _process_request(self, queued: QueuedRequest) -> None:
        """Process a single queued request."""
        if queued.future.cancelled():
            logger.info("Skipping cancelled request %s", queued.request_id)
            self._queue.task_done()
            return

        start_time = time.time()
        self._is_processing = True

        try:
            async with self._semaphore:
                if queued.future.cancelled():
                    logger.info("Skipping cancelled request %s after queue wait", queued.request_id)
                    return

                logger.info(
                    "Processing request %s (priority=%s, model=%s)",
                    queued.request_id, queued.priority.name, queued.request.model,
                )

                response = await self.client.chat_completion(queued.request)

                if not queued.future.done():
                    queued.future.set_result(response)

                elapsed_ms = (time.time() - start_time) * 1000
                self._update_stats(queued.priority, elapsed_ms)

                logger.info("Completed request %s in %.0fms", queued.request_id, elapsed_ms)

        except Exception as e:
            logger.error("Failed to process request %s: %s", queued.request_id, e, exc_info=True)
            if not queued.future.done():
                queued.future.set_exception(e)

        finally:
            self._is_processing = False
            self._queue.task_done()

    def _update_stats(self, priority: Priority, elapsed_ms: float) -> None:
        """Update statistics after processing a request."""
        self._stats["requests_processed"] += 1
        self._stats["total_response_time_ms"] += elapsed_ms
        self._response_times.append(elapsed_ms)

        if priority == Priority.HIGH:
            self._stats["requests_high"] += 1
        else:
            self._stats["requests_low"] += 1

    def get_stats(self) -> GatewayStats:
        """Get current gateway statistics."""
        avg_time = 0.0
        if self._response_times:
            avg_time = sum(self._response_times) / len(self._response_times)

        return GatewayStats(
            queue_size=self._queue.qsize(),
            requests_processed=self._stats["requests_processed"],
            requests_high_priority=self._stats["requests_high"],
            requests_low_priority=self._stats["requests_low"],
            avg_response_time_ms=avg_time,
            is_processing=self._is_processing,
            uptime_seconds=time.time() - self._start_time,
        )

    @property
    def queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Check if queue manager is running."""
        return self._running
